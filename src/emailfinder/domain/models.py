from dataclasses import dataclass
from enum import StrEnum


class PipelineState(StrEnum):
    DISCOVERED = "DISCOVERED"
    COMPANY_QUALIFIED = "COMPANY_QUALIFIED"
    COMPANY_REJECTED = "COMPANY_REJECTED"
    PERSON_FOUND = "PERSON_FOUND"
    PERSON_REJECTED = "PERSON_REJECTED"
    EMAIL_FOUND = "EMAIL_FOUND"
    EMAIL_NOT_FOUND = "EMAIL_NOT_FOUND"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    RISKY = "RISKY"
    REJECTED = "REJECTED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    SUPPRESSED = "SUPPRESSED"
    EXPORTED = "EXPORTED"


TRANSITIONS = {
    PipelineState.DISCOVERED: {PipelineState.COMPANY_QUALIFIED, PipelineState.COMPANY_REJECTED},
    PipelineState.COMPANY_QUALIFIED: {PipelineState.PERSON_FOUND, PipelineState.PERSON_REJECTED},
    PipelineState.PERSON_FOUND: {PipelineState.EMAIL_FOUND, PipelineState.EMAIL_NOT_FOUND},
    PipelineState.EMAIL_FOUND: {PipelineState.VERIFICATION_PENDING},
    PipelineState.VERIFICATION_PENDING: {PipelineState.VERIFIED, PipelineState.HIGH_CONFIDENCE, PipelineState.RISKY, PipelineState.REJECTED},
    PipelineState.VERIFIED: {PipelineState.READY_FOR_REVIEW},
    PipelineState.HIGH_CONFIDENCE: {PipelineState.READY_FOR_REVIEW},
    PipelineState.READY_FOR_REVIEW: {PipelineState.SUPPRESSED, PipelineState.EXPORTED},
}


def validate_transition(current: PipelineState, target: PipelineState) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid pipeline transition: {current} -> {target}")


class VerificationBand(StrEnum):
    VERIFIED = "VERIFIED"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    RISKY = "RISKY"
    REJECT = "REJECT"


@dataclass(frozen=True)
class VerificationResult:
    band: VerificationBand
    syntax_valid: bool
    domain_exists: bool
    mx_present: bool
    catch_all: bool
    mailbox_signal: str
    provider: str
    provider_score: int
    reason: str


@dataclass(frozen=True)
class Candidate:
    company_name: str
    domain: str
    website: str
    industry: str
    employee_range: str
    country: str
    contact_name: str
    title: str
    email: str
    icp_score: int
    buyer_score: int
    verification: VerificationResult
    evidence_url: str
    rejection_reason: str | None = None

