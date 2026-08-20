import httpx

from emailfinder.providers.wikidata import WikidataCompanyDiscoveryProvider


def test_wikidata_provider_parses_structured_company_and_honors_limit(brief):
    def handler(request):
        if request.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": [{"id": "Q123"}]})
        assert "LIMIT 1" in request.url.params["query"]
        return httpx.Response(200, json={"results": {"bindings": [{
            "company": {"value": "https://www.wikidata.org/entity/Q1"},
            "companyLabel": {"value": "Acme Engineering"},
            "website": {"value": "https://www.acme.test/about"},
            "industryLabel": {"value": "engineering"},
            "countryLabel": {"value": "United States"},
            "employees": {"value": "250"},
        }]}})
    provider = WikidataCompanyDiscoveryProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), limit=1)
    found = provider.discover(brief)
    assert len(found) == 1
    assert found[0].domain == "acme.test"
    assert "250 employees" in found[0].discovery_excerpt
