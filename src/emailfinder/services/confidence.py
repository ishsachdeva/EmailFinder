from emailfinder.domain.models import PipelineState, VerificationBand, VerificationResult


def confidence_gate(result: VerificationResult, minimum_score: int = 70) -> tuple[float, VerificationBand, PipelineState]:
    score = float(result.provider_score)
    allowed = result.band in {VerificationBand.VERIFIED, VerificationBand.HIGH_CONFIDENCE} and score >= minimum_score
    return score, result.band, PipelineState.READY_FOR_REVIEW if allowed else PipelineState.REJECTED

