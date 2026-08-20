import json

import httpx
import pytest
from pydantic import ValidationError

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.providers.brave import BraveCompanyDiscoveryProvider
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider, classify_source, normalize_evidence_text
from emailfinder.providers.nvidia import NVIDIAReasoningProvider, SlidingWindowRateLimiter


def company():
    return DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://acme.test/about", discovery_title="Acme Engineering | Home", discovery_excerpt="Engineering company")


def evidence():
    return [PublicEvidence(id="web-1", evidence_type="company_website", source_url="https://acme.test", source_title="Acme", excerpt="Acme Engineering provides industrial services across the United States with growing operations teams.", source_quality=SourceQuality.PRIMARY)]


def output(**updates):
    data = dict(company_name="Acme Engineering", domain="acme.test", industry_assessment="Engineering", geography_assessment="United States", size_assessment="50-200", positive_signals=["operations teams"], negative_signals=[], industry_fit=90, company_size_fit=80, geography_fit=100, workflow_signals=75, exclusion_risk=0, icp_score=84, qualification="ACCEPT", reason="Evidence supports fit", need_hypothesis="Growing operations may make approval coordination relevant.", evidence_ids_used=["web-1"], confidence=85)
    data.update(updates); return data


def test_brave_provider_parses_and_dedupes(brief):
    payload = {"web": {"results": [{"title": "Acme Engineering | Home", "url": "https://www.acme.test/about", "description": "Industrial engineering"}, {"title": "Acme duplicate", "url": "https://acme.test/services", "description": "Services"}, {"title": "Social", "url": "https://linkedin.com/company/acme", "description": ""}]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    found = BraveCompanyDiscoveryProvider(api_key="test-not-secret", client=client).discover(brief)
    assert len(found) == 1 and found[0].domain == "acme.test"


def test_evidence_normalization_and_source_quality():
    assert normalize_evidence_text(" A\n  useful\tclaim ") == "A useful claim"
    assert classify_source("https://careers.acme.test/jobs", "acme.test") == SourceQuality.PRIMARY
    assert classify_source("https://reuters.com/acme", "acme.test") == SourceQuality.REPUTABLE_SECONDARY
    assert classify_source("https://unknown.example/acme", "acme.test") == SourceQuality.WEAK_SECONDARY


def test_evidence_extractor_ignores_script_and_style_content():
    html = "<html><style>secret-css</style><script>trackingCode()</script><body>Acme Engineering provides useful industrial services across the United States for operational teams.</body></html>"
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"}, request=request)))
    collected = PublicWebsiteEvidenceProvider(client=client).collect(company())
    assert collected and "trackingCode" not in collected[0].excerpt and "secret-css" not in collected[0].excerpt


def test_nvidia_output_contract_rejects_bad_data():
    with pytest.raises(ValidationError): QualificationOutput.model_validate(output(icp_score=101))
    with pytest.raises(ValidationError): QualificationOutput.model_validate(output(need_hypothesis="", evidence_ids_used=[]))


def test_nvidia_parses_valid_structured_response(brief):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output())}}]})))
    provider = NVIDIAReasoningProvider(api_key="test", model="test-model", client=client, limiter=SlidingWindowRateLimiter(36), sleeper=lambda _: None)
    assert provider.qualify_company(company(), evidence(), brief).qualification == "ACCEPT"


def test_nvidia_default_timeout_allows_slow_reasoning_model():
    provider = NVIDIAReasoningProvider(api_key="test", model="test-model")
    assert provider.client.timeout.read == 240
    provider.client.close()


def test_nvidia_invalid_output_has_bounded_retries(brief):
    calls = 0
    def handler(request):
        nonlocal calls; calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    provider = NVIDIAReasoningProvider(api_key="test", model="test", client=httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=2, sleeper=lambda _: None)
    with pytest.raises(EmailFinderError) as caught: provider.qualify_company(company(), evidence(), brief)
    assert calls == 2 and caught.value.category == ErrorCategory.INVALID_RESULT


def test_rate_limiter_waits_at_boundary():
    state = {"now": 0.0}; waits = []
    def clock(): return state["now"]
    def sleep(seconds): waits.append(seconds); state["now"] += seconds
    limiter = SlidingWindowRateLimiter(1, clock=clock, sleeper=sleep)
    limiter.acquire(); limiter.acquire()
    assert waits == [60.0]
