from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emailfinder.domain.models import PipelineState, VerificationBand
from emailfinder.persistence.database import Evidence, Job, Prospect
from emailfinder.providers.mock import FIXTURES, MockCompanyDiscoveryProvider, MockEmailDiscoveryProvider, MockEmailVerificationProvider, MockReasoningProvider
from emailfinder.services.pipeline import MockPipeline


def test_mock_providers_are_deterministic(brief):
    discovery = MockCompanyDiscoveryProvider()
    assert discovery.discover(brief) == discovery.discover(brief) == FIXTURES
    assert MockEmailVerificationProvider().verify(FIXTURES[0].email).band == VerificationBand.VERIFIED


def test_end_to_end_mock_vertical_slice(engine, brief):
    pipeline = MockPipeline(engine, MockCompanyDiscoveryProvider(), MockReasoningProvider(), MockEmailDiscoveryProvider(), MockEmailVerificationProvider())
    results = pipeline.run(brief)
    assert len(results) == 5
    assert [p.status for p in results].count(PipelineState.READY_FOR_REVIEW) == 2
    assert {p.status for p in results} >= {PipelineState.COMPANY_REJECTED, PipelineState.PERSON_REJECTED, PipelineState.REJECTED}
    risky = next(p for p in results if p.confidence_band == VerificationBand.RISKY)
    assert risky.status != PipelineState.READY_FOR_REVIEW
    with Session(engine) as session:
        assert session.scalar(select(func.count(Evidence.id))) == 5
        job = session.scalar(select(Job))
        assert (job.status, job.processed_count, job.accepted_count, job.rejected_count) == ("COMPLETED", 5, 2, 3)

