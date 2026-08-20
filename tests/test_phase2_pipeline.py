from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emailfinder.domain.phase2 import CandidateSeed, DiscoveredCompany, DiscoveryLane, PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.persistence.database import Company, Evidence, Prospect
from emailfinder.services.phase2_pipeline import Phase2APipeline
from emailfinder.services.qualification import final_icp_score, hard_rejection


SEED = CandidateSeed(company_name="Acme Engineering", official_domain="acme.test", discovery_lane=DiscoveryLane.OFFICIAL_COMPANY_SIGNAL, trigger_name="operations manager", trigger_strength=95, trigger_source_url="https://acme.test/careers", trigger_source_type="OFFICIAL_COMPANY", trigger_excerpt="Acme Engineering is hiring an Operations Manager.", entity_confidence=100, query_used="fixture", seed_quality_score=98)
C = DiscoveredCompany(name="Acme Engineering", domain="acme.test", website="https://acme.test", discovery_url="https://acme.test", discovery_title="Acme Engineering", discovery_excerpt="Official site", entity_resolution_status="CONFIRMED", candidate_seed=SEED)
E = PublicEvidence(id="web-1", evidence_type="company_website", source_url="https://acme.test", source_title="Acme", excerpt="Acme Engineering is a United States industrial engineering company with 100 employees and procurement operations.", source_quality=SourceQuality.PRIMARY)


class Discovery:
    def discover(self, brief): return [C, C]
class EvidenceProvider:
    def collect(self, company): return [E]
class Reasoning:
    calls = 0
    def qualify_company(self, company, evidence, brief, resolved_facts=None):
        self.calls += 1
        return QualificationOutput(qualification="ACCEPT", model_score=90, confidence=90, positive_signals=[{"signal": "procurement operations", "evidence_ids": ["web-1"]}], negative_signals=[], reason="Primary evidence supports the fit", need_hypothesis="Documented procurement operations suggest approval coordination may be relevant.", need_hypothesis_evidence_ids=["web-1"])


def test_hard_rejection_before_reasoning(brief):
    excluded = E.model_copy(update={"excerpt": "Acme Engineering is a Gambling operator in the United States."})
    brief.icp.excluded_industries = ["Gambling"]
    assert hard_rejection(C, [excluded], brief).startswith("REJECT")


def test_explicit_company_size_hard_rejection(brief):
    too_small = E.model_copy(update={"excerpt": "Acme Engineering is a team of 5 employees in the United States."})
    assert "below configured minimum" in hard_rejection(C, [too_small], brief)


def test_documented_final_score_contract():
    assert final_icp_score(55, 100) == 100
    assert final_icp_score(0, 100) == 30


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
        assert session.scalar(select(func.count(Evidence.id))) == 2


def test_explicit_primary_employee_count_rejects_before_reasoning(engine, brief):
    candidate = C
    class OneDiscovery:
        def discover(self, brief): return [candidate]
    class NoSizeEvidence:
        def collect(self, company):
            return [E.model_copy(update={"excerpt": "Acme Engineering is a United States engineering company with 5 employees and procurement operations."})]
    reasoning = Reasoning()
    job = Phase2APipeline(engine, OneDiscovery(), NoSizeEvidence(), reasoning).run(brief)
    assert job.rejected_count == 1, (job.insufficient_count, job.error_count, job.processed_count)
    assert reasoning.calls == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count(Evidence.id))) == 2


def test_only_confirmed_entities_reach_qualification(engine, brief):
    unresolved = C.model_copy(update={"entity_resolution_status": "PROBABLE"})
    class UnsafeDiscovery:
        def discover(self, brief): return [unresolved]
    reasoning = Reasoning()
    job = Phase2APipeline(engine, UnsafeDiscovery(), EvidenceProvider(), reasoning).run(brief)
    assert job.discovered_count == 0 and reasoning.calls == 0
