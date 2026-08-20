import json

import httpx
import pytest
from pydantic import ValidationError

from emailfinder.domain.errors import EmailFinderError, ErrorCategory
from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.providers.brave import BraveCompanyDiscoveryProvider
from emailfinder.providers.evidence import PublicWebsiteEvidenceProvider, classify_source, normalize_evidence_text
from emailfinder.providers.nvidia import NVIDIAReasoningProvider, SlidingWindowRateLimiter, ValidationFailureCategory, schema_issue


def company():
    return DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://acme.test/about", discovery_title="Acme Engineering | Home", discovery_excerpt="Engineering company")


def evidence():
    return [PublicEvidence(id="web-1", evidence_type="company_website", source_url="https://acme.test", source_title="Acme", excerpt="Acme Engineering provides industrial services across the United States with growing operations teams.", source_quality=SourceQuality.PRIMARY)]


def output(**updates):
    data = dict(qualification="ACCEPT", model_score=84, confidence=85, positive_signals=[{"signal": "operations teams", "evidence_ids": ["web-1"]}], negative_signals=[], reason="Evidence supports fit", need_hypothesis="Growing operations may make approval coordination relevant.", need_hypothesis_evidence_ids=["web-1"])
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
    with pytest.raises(ValidationError): QualificationOutput.model_validate(output(model_score=101))
    with pytest.raises(ValidationError): QualificationOutput.model_validate(output(need_hypothesis=None, need_hypothesis_evidence_ids=[]))
    rejected = output(qualification="REJECT", need_hypothesis=None, need_hypothesis_evidence_ids=[])
    assert QualificationOutput.model_validate(rejected).need_hypothesis is None
    with pytest.raises(ValidationError): QualificationOutput.model_validate(output(qualification="INSUFFICIENT_EVIDENCE", need_hypothesis="Likely complex approvals"))


def test_nvidia_parses_valid_structured_response(brief):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output())}}]})))
    provider = NVIDIAReasoningProvider(api_key="test", model="test-model", client=client, limiter=SlidingWindowRateLimiter(36), sleeper=lambda _: None)
    assert provider.qualify_company(company(), evidence(), brief).qualification == "ACCEPT"
    assert provider.call_metrics[0]["validation_success"] is True


def test_nvidia_simplified_contract_is_bounded(brief):
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output())}}]})
    provider = NVIDIAReasoningProvider(api_key="test", model="test-model", client=httpx.Client(transport=httpx.MockTransport(handler)))
    provider.qualify_company(company(), evidence(), brief)
    prompt = json.loads(captured["messages"][1]["content"])
    assert isinstance(prompt["output_contract"], str) and len(prompt["evidence"]) <= 5
    assert captured["response_format"]["type"] == "json_schema"


def test_nvidia_default_timeout_allows_slow_reasoning_model():
    provider = NVIDIAReasoningProvider(api_key="test", model="test-model")
    assert provider.client.timeout.read == 240
    provider.client.close()


def test_nvidia_invalid_output_has_bounded_retries(brief):
    calls = 0; prompts = []
    def handler(request):
        nonlocal calls; calls += 1; prompts.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    provider = NVIDIAReasoningProvider(api_key="test", model="test", client=httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=2, sleeper=lambda _: None)
    with pytest.raises(EmailFinderError) as caught: provider.qualify_company(company(), evidence(), brief)
    assert calls == 2 and caught.value.category == ErrorCategory.INVALID_RESULT
    assert "repair" not in prompts[0] and "INVALID_JSON" in prompts[1]["repair"]
    assert provider.call_metrics[0]["failure_category"] == ValidationFailureCategory.INVALID_JSON


@pytest.mark.parametrize(("updates", "category"), [
    ({"model_score": 101}, ValidationFailureCategory.SCORE_OUT_OF_RANGE),
    ({"model_score": "high"}, ValidationFailureCategory.WRONG_FIELD_TYPE),
    ({"qualification": "MAYBE"}, ValidationFailureCategory.INVALID_ENUM),
    ({"unexpected": True}, ValidationFailureCategory.EXTRA_FIELD_NOT_ALLOWED),
])
def test_nvidia_schema_failure_categories(updates, category):
    data = output(); data.update(updates)
    with pytest.raises(ValidationError) as caught: QualificationOutput.model_validate(data)
    assert schema_issue(caught.value).category == category


def test_nvidia_missing_field_and_need_rule_categories():
    missing = output(); missing.pop("model_score")
    with pytest.raises(ValidationError) as caught: QualificationOutput.model_validate(missing)
    assert schema_issue(caught.value).category == ValidationFailureCategory.MISSING_REQUIRED_FIELD
    missing_evidence = output(positive_signals=[{"signal": "claim", "evidence_ids": []}])
    with pytest.raises(ValidationError) as caught: QualificationOutput.model_validate(missing_evidence)
    assert schema_issue(caught.value).category == ValidationFailureCategory.MISSING_REQUIRED_EVIDENCE
    bad_need = output(qualification="REJECT", need_hypothesis="speculation", need_hypothesis_evidence_ids=["web-1"])
    with pytest.raises(ValidationError) as caught: QualificationOutput.model_validate(bad_need)
    assert schema_issue(caught.value).category == ValidationFailureCategory.UNSUPPORTED_NEED_HYPOTHESIS


def test_nvidia_evidence_validation_categories(brief):
    responses = [output(positive_signals=[{"signal": "claim", "evidence_ids": ["unknown"]}]), output(positive_signals=[{"signal": "claim", "evidence_ids": ["web-1", "web-1"]}])]
    for expected, data in zip((ValidationFailureCategory.UNKNOWN_EVIDENCE_ID, ValidationFailureCategory.DUPLICATE_EVIDENCE_ID), responses):
        client = httpx.Client(transport=httpx.MockTransport(lambda request, d=data: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(d)}}]})))
        provider = NVIDIAReasoningProvider(api_key="test", model="test", client=client, max_attempts=1)
        with pytest.raises(EmailFinderError): provider.qualify_company(company(), evidence(), brief)
        assert provider.call_metrics[0]["failure_category"] == expected


def test_nvidia_empty_response_category(brief):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})))
    provider = NVIDIAReasoningProvider(api_key="test", model="test", client=client, max_attempts=1)
    with pytest.raises(EmailFinderError): provider.qualify_company(company(), evidence(), brief)
    assert provider.call_metrics[0]["failure_category"] == ValidationFailureCategory.EMPTY_RESPONSE


def test_rate_limiter_waits_at_boundary():
    state = {"now": 0.0}; waits = []
    def clock(): return state["now"]
    def sleep(seconds): waits.append(seconds); state["now"] += seconds
    limiter = SlidingWindowRateLimiter(1, clock=clock, sleeper=sleep)
    limiter.acquire(); limiter.acquire()
    assert waits == [60.0]
