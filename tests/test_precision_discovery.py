import json

import httpx

from emailfinder.domain.phase2 import DiscoveredCompany, DiscoveryLane
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider
from emailfinder.providers.tavily import TavilyCompanyDiscoveryProvider


def client_for(search_results, pages):
    def handler(request):
        if request.method == "POST": return httpx.Response(200, json={"results": search_results})
        return httpx.Response(200, text=pages.get(request.url.host + request.url.path, pages.get(request.url.host, "missing")), headers={"content-type": "text/html"}, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_amazon_india_navigation_cannot_become_company(brief):
    result = {"title": "Manufacturing procurement", "url": "https://business.amazon.com/en/blog/manufacturing-procurement", "content": "Change country India", "score": .9}
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client_for([result], {}), raw_limit=1)
    assert provider.discover(brief) == []
    identity_client = client_for([], {"business.amazon.in": "<title>Amazon Business</title><h1>Amazon Business</h1>"})
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=identity_client)
    assert provider._resolve_canonical("India", "https://business.amazon.in") is None


def test_builtin_listicle_companies_are_not_automatic_seeds(brief):
    result = {"title": "Top Engineering Companies", "url": "https://builtin.com/articles/engineering", "content": "Snap Klaviyo Sonar", "score": .9}
    html = '<a href="https://snap.com">Snap Inc.</a><a href="https://klaviyo.com">Klaviyo</a><a href="https://sonarsource.com">Sonar</a>'
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client_for([result], {"builtin.com/articles/engineering": html}), raw_limit=1)
    assert provider.discover(brief) == []


def test_procurement_jobposting_creates_ranked_seed(brief):
    brief.qualification.strong_triggers.append("procurement manager")
    result = {"title": "Procurement Manager job", "url": "https://jobs.test/jobs/123", "content": "Lindum is hiring", "score": .9}
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Procurement Manager", "datePosted": "2026-08-01", "description": "Lead procurement processes", "hiringOrganization": {"@type": "Organization", "name": "Lindum Group Ltd", "sameAs": "https://lindumgroup.com"}}
    pages = {"jobs.test/jobs/123": f'<script type="application/ld+json">{json.dumps(posting)}</script>', "lindumgroup.com": '<script type="application/ld+json">{"@type":"Organization","name":"Lindum Group Ltd","url":"https://lindumgroup.com"}</script><title>Lindum Group</title><h1>Lindum Group Ltd</h1>'}
    provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client_for([result], pages), raw_limit=1)
    found = provider.discover(brief)
    assert len(found) == 1 and found[0].name == "Lindum Group Ltd"
    assert found[0].candidate_seed.discovery_lane == DiscoveryLane.JOB_TRIGGER
    assert found[0].candidate_seed.trigger_name == "procurement manager" and found[0].candidate_seed.trigger_date == "2026-08-01"
    assert found[0].candidate_seed.seed_quality_score >= 80


def test_irrelevant_job_and_missing_employer_create_no_seed(brief):
    result = {"title": "Job", "url": "https://jobs.test/jobs/1", "content": "job", "score": .9}
    base = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Software Engineer", "description": "Build software", "hiringOrganization": {"name": "Lindum Group Ltd", "sameAs": "https://lindumgroup.com"}}
    for posting in (base, {**base, "title": "Operations Manager", "hiringOrganization": {}}):
        pages = {"jobs.test/jobs/1": f'<script type="application/ld+json">{json.dumps(posting)}</script>'}
        provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client_for([result], pages), raw_limit=1)
        assert provider.discover(brief) == []


def test_official_company_signal_requires_tier_a_content(brief):
    result = {"title": "Acme Engineering | Careers", "url": "https://acme.test/careers/", "content": "careers", "score": .9}
    identity = '<script type="application/ld+json">{"@type":"Organization","name":"Acme Engineering","url":"https://acme.test"}</script><title>Acme Engineering</title><h1>Acme Engineering</h1>'
    for suffix, expected in (("We are hiring an Operations Manager.", 1), ("We are hiring a Software Engineer.", 0)):
        provider = TavilyCompanyDiscoveryProvider(api_key="test", client=client_for([result], {"acme.test/careers/": identity + suffix, "acme.test": identity}), raw_limit=1)
        found = provider.discover(brief)
        assert len(found) == expected
        if found: assert found[0].candidate_seed.discovery_lane == DiscoveryLane.OFFICIAL_COMPANY_SIGNAL


def test_targeted_evidence_search_is_domain_restricted_and_bounded():
    company = DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://acme.test/careers", discovery_title="Acme", discovery_excerpt="Operations Manager", entity_resolution_status="CONFIRMED")
    posts = []
    def search_handler(request):
        posts.append(json.loads(request.content)); return httpx.Response(200, json={"results": [{"title": "Acme About", "url": "https://acme.test/about"}, {"title": "Wrong", "url": "https://other.test/acme"}]})
    def page_handler(request):
        text = "Acme Engineering has 250 employees at its United States headquarters. " * 3
        return httpx.Response(200, text=text, headers={"content-type": "text/html"}, request=request)
    provider = PublicWebsiteEvidenceProvider(client=httpx.Client(transport=httpx.MockTransport(page_handler)), search_client=httpx.Client(transport=httpx.MockTransport(search_handler)), api_key="test", page_limit=1, targeted_search_budget=2)
    evidence = provider.collect(company)
    assert len(posts) <= 2 and all(post["search_depth"] == "basic" and post["include_domains"] == [company.domain] for post in posts)
    assert all("other.test" not in str(item.source_url) for item in evidence)
