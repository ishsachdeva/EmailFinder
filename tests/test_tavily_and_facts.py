import httpx

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, SourceQuality
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider
from emailfinder.providers.tavily import SearchResultType, TavilyCompanyDiscoveryProvider, build_signal_queries, classify_search_result, extract_company_entities
from emailfinder.services.facts import resolve_facts, resolved_hard_rejection


def ev(eid, text, quality, url="https://acme.test"):
    return PublicEvidence(id=eid, evidence_type="company_website", source_url=url, source_title=eid, excerpt=text, source_quality=quality)


def test_signal_queries_derive_from_brief(brief):
    queries = build_signal_queries(brief, 2)
    assert brief.icp.target_industries[0] in queries[0]
    assert brief.icp.target_geographies[0] in queries[0]
    assert brief.qualification.positive_signals[0] in queries[0]


def test_tavily_parses_filters_and_dedupes(brief):
    payload = {"results": [{"title": "Acme Engineering | Procurement", "url": "https://www.acme.test/operations", "content": "Acme describes procurement operations and manufacturing facilities.", "score": .9}, {"title": "Duplicate", "url": "https://acme.test/about", "content": "More useful company evidence about Acme operations.", "score": .5}, {"title": "LinkedIn", "url": "https://linkedin.com/company/acme", "content": "Blocked social result with enough content.", "score": 1}]}
    def handler(request):
        if request.method == "GET": return httpx.Response(200, text="<html>Acme Engineering industrial operations</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json=payload)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client, raw_limit=3, candidate_limit=5)
    found = provider.discover(brief)
    assert len(found) == 1 and found[0].domain == "acme.test"
    assert provider.raw_result_count == 3 and provider.unique_domain_count == 1
    assert found[0].discovery_excerpt.startswith("SEARCH_DISCOVERY")


def test_vendor_article_and_builtin_listicle_are_not_host_candidates():
    amazon = {"title": "Procurement leaders modernize operations", "url": "https://business.amazon.com/blog/procurement", "content": "A vendor article for procurement teams."}
    builtin = {"title": "Top 20 Engineering Companies in London", "url": "https://builtin.com/articles/engineering-companies", "content": "A roundup of engineering firms."}
    assert classify_search_result(amazon) == SearchResultType.VENDOR_CONTENT
    assert classify_search_result(builtin) == SearchResultType.LISTICLE


def test_directory_is_not_target_and_listicle_entities_are_bounded():
    directory = {"title": "Company Directory", "url": "https://clutch.co/directory", "content": "Find businesses and company profiles."}
    listicle = {"title": "Top engineering firms", "url": "https://example.test/list", "content": "Acme Engineering and Beta Industrial Services are growing. Gamma Logistics operates nationally."}
    assert classify_search_result(directory) == SearchResultType.DIRECTORY
    assert extract_company_entities(listicle) == ["Acme Engineering", "Beta Industrial Services", "Gamma Logistics"]


def test_official_domain_resolution_validation_and_source_separation(brief):
    discovery = {"title": "Top Engineering Companies", "url": "https://builtin.com/list", "content": "Acme Engineering builds industrial systems.", "score": .9}
    official = {"title": "Acme Engineering | Official Website", "url": "https://acme.test/about", "content": "Acme Engineering industrial operations", "score": 1}
    def handler(request):
        if request.method == "GET": return httpx.Response(200, text="<html>Acme Engineering industrial operations</html>", headers={"content-type": "text/html"})
        body = __import__("json").loads(request.content)
        return httpx.Response(200, json={"results": [discovery] if "official website" not in body["query"] else [official]})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)), raw_limit=1)
    found = provider.discover(brief)
    assert len(found) == 1
    assert found[0].domain == "acme.test" and str(found[0].discovery_url).startswith("https://builtin.com/")
    assert found[0].resolution_source == "https://acme.test/about" and found[0].domain_validation_status == "VALIDATED"


def test_unresolved_entity_never_becomes_candidate_and_duplicate_domain_dedupes(brief):
    source = {"title": "Industrial roundup", "url": "https://builtin.com/list", "content": "Acme Engineering and Beta Engineering", "score": .8}
    official = {"title": "Engineering website", "url": "https://acme.test", "content": "Acme Engineering Beta Engineering", "score": 1}
    calls = 0
    def handler(request):
        if request.method == "GET": return httpx.Response(200, text="<html>Acme Engineering industrial operations</html>", headers={"content-type": "text/html"})
        nonlocal calls; calls += 1
        if calls == 1: return httpx.Response(200, json={"results": [source]})
        return httpx.Response(200, json={"results": [official] if "Acme" in __import__("json").loads(request.content)["query"] else []})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)), raw_limit=1)
    found = provider.discover(brief)
    assert [c.domain for c in found] == ["acme.test"]
    assert provider.metrics["unresolved_entities"] == 1


def test_company_domain_validation_rejects_captured_false_matches(brief):
    source = {"title": "Top Engineering Companies", "url": "https://builtin.com/list", "content": "Babcock International Group and China State Construction Engineering", "score": .9}
    bad = {"title": "Industry resource", "url": "https://maritimeindustries.org", "content": "Babcock International Group", "score": 1}
    def handler(request):
        if request.method == "GET": return httpx.Response(200, text="<html>Society of Maritime Industries member association</html>", headers={"content-type": "text/html"})
        query = __import__("json").loads(request.content)["query"]
        return httpx.Response(200, json={"results": [source] if "official website" not in query else [bad]})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)), raw_limit=1)
    assert provider.discover(brief) == []
    assert provider.metrics["unresolved_entities"] >= 2


def test_internal_link_discovery_is_bounded_and_ranked():
    pages = {"/": '<a href="/privacy">Privacy</a><a href="/investor-relations">Investors</a><a href="/about-us">About</a>' + " Acme Engineering homepage operations " * 5, "/investor-relations": "Acme Engineering has 250 employees in the United States. " * 3, "/about-us": "Acme Engineering is a manufacturing company. " * 3}
    def handler(request): return httpx.Response(200, text=pages.get(request.url.path, "missing"), headers={"content-type": "text/html"}, request=request)
    company = DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://source.test/acme", discovery_title="Acme", discovery_excerpt="signal")
    evidence = PublicWebsiteEvidenceProvider(httpx.Client(transport=httpx.MockTransport(handler)), page_limit=2).collect(company)
    assert {e.evidence_type for e in evidence} >= {"company_website", "investor_page", "company_detail_page"}
    assert all("privacy" not in str(e.source_url) for e in evidence)


def test_primary_geography_overrides_secondary_quiqup_regression(brief):
    secondary = ev("secondary", "Quiqup logistics; United Kingdom", SourceQuality.REPUTABLE_SECONDARY, "https://www.wikidata.org/entity/Q1")
    primary = ev("primary", "Quiqup Delivery LLC is headquartered in Dubai, UAE and serves the United Arab Emirates.", SourceQuality.PRIMARY, "https://quiqup.com")
    facts = resolve_facts([secondary, primary], brief)
    assert facts.geography.value == "United Arab Emirates" and facts.geography.conflict
    assert resolved_hard_rejection(facts, brief).startswith("REJECT")


def test_missing_size_and_geography_are_not_rejections(brief):
    facts = resolve_facts([ev("web", "Acme Engineering provides procurement operations services.", SourceQuality.PRIMARY)], brief)
    assert facts.employee_count.value is None and facts.geography.value is None
    assert resolved_hard_rejection(facts, brief) is None
