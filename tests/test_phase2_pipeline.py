from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emailfinder.domain.phase2 import DiscoveredCompany, PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.persistence.database import Company, Evidence, Prospect
from emailfinder.services.phase2_pipeline import Phase2APipeline
from emailfinder.services.qualification import hard_rejection, inspectable_score


C = DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://acme.test", discovery_title="Acme Engineering", discovery_excerpt="Official site")
E = PublicEvidence(id="web-1", evidence_type="company_website", source_url="https://acme.test", source_title="Acme", excerpt="Acme Engineering is a United States industrial engineering company with 100 employees and procurement operations.", source_quality=SourceQuality.PRIMARY)


class Discovery:
    def discover(self, brief): return [C, C]
class EvidenceProvider:
    def collect(self, company): return [E]
class Reasoning:
    calls = 0
    def qualify_company(self, company, evidence, brief, resolved_facts=None):
        self.calls += 1
        return QualificationOutput(company_name="Acme Engineering", domain="acme.test", industry_assessment="Engineering", geography_assessment="United States", size_assessment="100", positive_signals=["procurement operations"], negative_signals=[], industry_fit=100, company_size_fit=100, geography_fit=100, workflow_signals=80, exclusion_risk=0, icp_score=90, qualification="ACCEPT", reason="Primary evidence supports the fit", need_hypothesis="Documented procurement operations suggest approval coordination may be relevant.", evidence_ids_used=["web-1"], confidence=90)


def test_hard_rejection_before_reasoning(brief):
    excluded = E.model_copy(update={"excerpt": "Acme Engineering is a Gambling operator in the United States."})
    brief.icp.excluded_industries = ["Gambling"]
    assert hard_rejection(C, [excluded], brief).startswith("REJECT")


def test_explicit_company_size_hard_rejection(brief):
    too_small = E.model_copy(update={"excerpt": "Acme Engineering is a team of 5 employees in the United States."})
    assert "below configured minimum" in hard_rejection(C, [too_small], brief)


def test_inspectable_weighted_score(brief):
    result = Reasoning().qualify_company(C, [E], brief)
    assert inspectable_score(result, brief) == 94


def test_phase2_orchestration_dedupes_and_caches(engine, brief):
    reasoning = Reasoning()
    pipeline = Phase2APipeline(engine, Discovery(), EvidenceProvider(), reasoning)
    first = pipeline.run(brief)
    second = pipeline.run(brief)
    assert first.discovered_count == 2 and first.accepted_count == 1
    assert reasoning.calls == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count(Company.id))) == 1
        assert session.scalar(select(func.count(Prospect.id))) == 1
        assert session.scalar(select(func.count(Evidence.id))) == 1


def test_wikidata_employee_count_can_reject_before_reasoning(engine, brief):
    candidate = C.model_copy(update={"discovery_url": "https://www.wikidata.org/entity/Q1", "discovery_excerpt": "engineering; United States; 5 employees"})
    class OneDiscovery:
        def discover(self, brief): return [candidate]
    class NoSizeEvidence:
        def collect(self, company):
            return [E.model_copy(update={"excerpt": "Acme Engineering is a United States industrial engineering company with procurement operations."})]
    reasoning = Reasoning()
    job = Phase2APipeline(engine, OneDiscovery(), NoSizeEvidence(), reasoning).run(brief)
    assert job.rejected_count == 1, (job.insufficient_count, job.error_count, job.processed_count)
    assert reasoning.calls == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count(Evidence.id))) == 2
