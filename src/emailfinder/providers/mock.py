from dataclasses import replace

from emailfinder.domain.models import Candidate, VerificationBand, VerificationResult


def vr(band: VerificationBand, score: int, reason: str) -> VerificationResult:
    return VerificationResult(band, True, True, band != VerificationBand.REJECT, band == VerificationBand.RISKY, band.value, "MOCK_FIXTURE", score, reason)


FIXTURES = [
    Candidate("Northstar Labs", "northstar.test", "https://northstar.test", "Software", "51-200", "United States", "Avery Chen", "Chief Operating Officer", "avery@northstar.test", 91, 94, vr(VerificationBand.VERIFIED, 98, "Mock mailbox signal"), "https://fixture.test/northstar"),
    Candidate("BrightPath Advisory", "brightpath.test", "https://brightpath.test", "Professional Services", "20-50", "India", "Riya Shah", "VP Operations", "riya@brightpath.test", 82, 87, vr(VerificationBand.HIGH_CONFIDENCE, 85, "Mock strong domain and mailbox signals"), "https://fixture.test/brightpath"),
    Candidate("Lucky Bet", "luckybet.test", "https://luckybet.test", "Gambling", "51-200", "United States", "Sam Lee", "COO", "sam@luckybet.test", 25, 90, vr(VerificationBand.VERIFIED, 97, "Mock mailbox signal"), "https://fixture.test/luckybet", "Excluded industry"),
    Candidate("OpsCloud", "opscloud.test", "https://opscloud.test", "Software", "201-500", "United States", "Taylor Kim", "Engineering Intern", "taylor@opscloud.test", 88, 20, vr(VerificationBand.VERIFIED, 96, "Mock mailbox signal"), "https://fixture.test/opscloud", "Excluded buyer title"),
    Candidate("ServiceSpring", "servicespring.test", "https://servicespring.test", "Professional Services", "51-200", "India", "Jordan Rao", "Operations Director", "jordan@servicespring.test", 78, 72, vr(VerificationBand.RISKY, 52, "Mock catch-all domain"), "https://fixture.test/servicespring", "Verification inconclusive"),
]


class MockCompanyDiscoveryProvider:
    def discover(self, brief):
        return list(FIXTURES[: brief.prospecting.maximum_candidates_to_process])


class MockCompanyEnrichmentProvider:
    def enrich(self, candidate): return candidate


class MockPersonDiscoveryProvider:
    def find_person(self, candidate): return candidate


class MockEmailDiscoveryProvider:
    def find_email(self, candidate): return candidate.email


class MockEmailVerificationProvider:
    def verify(self, email):
        return next((c.verification for c in FIXTURES if c.email == email), vr(VerificationBand.REJECT, 0, "Unknown mock address"))


class MockReasoningProvider:
    def qualify(self, candidate, brief): return candidate.icp_score, candidate.buyer_score

