import httpx

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, SourceQuality
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider
from emailfinder.providers.tavily import DomainType, ResolutionStatus, SearchResultType, TavilyCompanyDiscoveryProvider, build_signal_queries, classify_domain_type, classify_search_result, company_name_sane, extract_company_entities, extract_linked_company_entities
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
    assert classify_search_result(directory) == SearchResultType.DIRECTORY
    assert extract_company_entities({"title": "Top engineering firms", "content": "Acme Engineering"}) == []
    html = '<h2>Top Engineering</h2><a href="https://acme.test">Acme Engineering</a><a href="/publisher/acme">Acme profile</a>'
    leads = extract_linked_company_entities(html, "https://publisher.test/list")
    assert [(lead.name, lead.destination_url) for lead in leads] == [("Acme Engineering", "https://acme.test")]


def test_official_domain_resolution_validation_and_source_separation(brief):
    discovery = {"title": "Top Engineering Companies", "url": "https://publisher.test/list", "content": "A bounded list.", "score": .9}
    def handler(request):
        if request.method == "POST": return httpx.Response(200, json={"results": [discovery]})
        if request.url.host == "publisher.test": return httpx.Response(200, text='<section>Industrial operations <a href="https://acme.test">Acme Engineering</a></section>', headers={"content-type": "text/html"})
        return httpx.Response(200, text="<html><title>Acme Engineering</title><h1>Acme Engineering</h1></html>", headers={"content-type": "text/html"})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)), raw_limit=1)
    found = provider.discover(brief)
    assert len(found) == 1
    assert found[0].domain == "acme.test" and str(found[0].discovery_url).startswith("https://publisher.test/")
    assert found[0].entity_resolution_status == "CONFIRMED"
    assert "Industrial operations" in found[0].discovery_excerpt


def test_unresolved_entity_never_becomes_candidate_and_duplicate_domain_dedupes(brief):
    source = {"title": "Industrial roundup", "url": "https://publisher.test/list", "content": "Linked organizations", "score": .8}
    def handler(request):
        if request.method == "POST": return httpx.Response(200, json={"results": [source]})
        if request.url.host == "publisher.test": return httpx.Response(200, text='<a href="https://acme.test">Acme Engineering</a><a href="https://acme.test/about">Acme Engineering</a><a href="https://unknown.test">Beta Engineering</a>', headers={"content-type": "text/html"})
        text = "Acme Engineering" if request.url.host == "acme.test" else "Unrelated brand"
        return httpx.Response(200, text=f"<title>{text}</title><h1>{text}</h1>", headers={"content-type": "text/html"})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)), raw_limit=1)
    found = provider.discover(brief)
    assert [c.domain for c in found] == ["acme.test"]
    assert provider.metrics["unresolved_entities"] == 1 and provider.metrics["duplicates_removed"] == 1


def test_company_domain_validation_rejects_captured_false_matches(brief):
    for malformed in ("The Biggest Civil Engineering", "Civil Engineering", "Top Engineering", "REQUEST PRICING Engineering"):
        assert not company_name_sane(malformed)
    assert classify_domain_type("asce.org", "American Society of Civil Engineers professional association") == DomainType.ASSOCIATION
    assert classify_domain_type("cadcrowd.com", "A marketplace for hiring freelancers") == DomainType.MARKETPLACE
    assert classify_domain_type("opensanctions.org", "Open database of sanctions; browse all data sources") == DomainType.DATABASE


def test_known_genuine_company_domain_fixtures_are_confirmed():
    pages = {"lindumgroup.com": "<title>Lindum Group</title><h1>Lindum Group Ltd</h1>", "sweco.co.uk": "<title>Sweco UK</title><h1>Sweco UK engineering consultants</h1>"}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=pages[request.url.host], headers={"content-type": "text/html"})))
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client)
    assert provider._evaluate_domain("Lindum Group Ltd", "https://lindumgroup.com").status == ResolutionStatus.CONFIRMED
    assert provider._evaluate_domain("Sweco UK", "https://sweco.co.uk").status == ResolutionStatus.CONFIRMED


def test_probable_and_unresolved_domains_do_not_confirm():
    pages = {"acme.test": "<title>Acme Engineering</title>", "unrelated.test": "<title>Unrelated Brand</title>"}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=pages[request.url.host], headers={"content-type": "text/html"})))
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client)
    assert provider._evaluate_domain("Acme Northstar Engineering", "https://acme.test").status == ResolutionStatus.PROBABLE
    assert provider._evaluate_domain("Northstar Engineering", "https://unrelated.test").status == ResolutionStatus.UNRESOLVED


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
