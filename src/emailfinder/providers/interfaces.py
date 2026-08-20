from typing import Protocol

from emailfinder.config.brief import CompanyBrief
from emailfinder.domain.models import Candidate, VerificationResult


class CompanyDiscoveryProvider(Protocol):
    def discover(self, brief: CompanyBrief) -> list[Candidate]: ...


class CompanyEnrichmentProvider(Protocol):
    def enrich(self, candidate: Candidate) -> Candidate: ...


class PersonDiscoveryProvider(Protocol):
    def find_person(self, candidate: Candidate) -> Candidate: ...


class EmailDiscoveryProvider(Protocol):
    def find_email(self, candidate: Candidate) -> str | None: ...


class EmailVerificationProvider(Protocol):
    def verify(self, email: str) -> VerificationResult: ...


class ReasoningProvider(Protocol):
    def qualify(self, candidate: Candidate, brief: CompanyBrief) -> tuple[int, int]: ...

