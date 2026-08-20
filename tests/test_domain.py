import pytest

from emailfinder.domain.models import PipelineState, VerificationBand, VerificationResult, validate_transition
from emailfinder.services.confidence import confidence_gate


def result(band, score):
    return VerificationResult(band, True, True, True, False, "mock", "MOCK", score, "fixture")


def test_valid_and_invalid_transitions():
    validate_transition(PipelineState.DISCOVERED, PipelineState.COMPANY_QUALIFIED)
    with pytest.raises(ValueError):
        validate_transition(PipelineState.DISCOVERED, PipelineState.EXPORTED)


@pytest.mark.parametrize("band,score,expected", [
    (VerificationBand.VERIFIED, 70, PipelineState.READY_FOR_REVIEW),
    (VerificationBand.HIGH_CONFIDENCE, 70, PipelineState.READY_FOR_REVIEW),
    (VerificationBand.HIGH_CONFIDENCE, 69, PipelineState.REJECTED),
    (VerificationBand.RISKY, 99, PipelineState.REJECTED),
    (VerificationBand.REJECT, 100, PipelineState.REJECTED),
])
def test_confidence_boundaries(band, score, expected):
    assert confidence_gate(result(band, score), 70)[2] == expected

