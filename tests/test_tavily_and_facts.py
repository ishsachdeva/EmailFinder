import httpx

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, SourceQuality
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider
from emailfinder.providers.tavily import TavilyCompanyDiscoveryProvider, build_signal_queries
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
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client, raw_limit=3, candidate_limit=5)
    found = provider.discover(brief)
    assert len(found) == 1 and found[0].domain == "acme.test"
    assert provider.raw_result_count == 3 and provider.unique_domain_count == 1
    assert found[0].discovery_excerpt.startswith("SEARCH_DISCOVERY")


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
