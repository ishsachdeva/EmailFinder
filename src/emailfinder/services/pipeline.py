import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from emailfinder.domain.models import PipelineState, VerificationBand
from emailfinder.persistence.database import Company, Email, Evidence, Job, Person, Prospect, is_suppressed, normalize_domain, normalize_email, now
from emailfinder.services.confidence import confidence_gate

log = logging.getLogger(__name__)


class MockPipeline:
    def __init__(self, engine, discovery, reasoning, email_discovery, verification):
        self.engine, self.discovery, self.reasoning = engine, discovery, reasoning
        self.email_discovery, self.verification = email_discovery, verification

    def run(self, brief) -> list[Prospect]:
        with Session(self.engine) as session:
            job = Job(job_type="MOCK_BUILD_TODAY", status="RUNNING")
            session.add(job); session.flush()
            results = []
            for c in self.discovery.discover(brief):
                domain = normalize_domain(c.domain)
                company = session.scalar(select(Company).where(Company.domain == domain))
                if not company:
                    company = Company(name=c.company_name, domain=domain, website=c.website, industry=c.industry, employee_range=c.employee_range, country=c.country, status=PipelineState.DISCOVERED)
                    session.add(company); session.flush()
                icp, buyer = self.reasoning.qualify(c, brief)
                person = session.scalar(select(Person).where(Person.company_id == company.id, Person.full_name == c.contact_name))
                if not person:
                    person = Person(company_id=company.id, full_name=c.contact_name, title=c.title, source_url=c.evidence_url, buyer_role_score=buyer, status=PipelineState.PERSON_FOUND)
                    session.add(person); session.flush()
                address = normalize_email(self.email_discovery.find_email(c) or "")
                email = session.scalar(select(Email).where(Email.email == address)) if address else None
                result = self.verification.verify(address) if address else c.verification
                if address and not email:
                    email = Email(person_id=person.id, email=address, discovery_method="MOCK_FIXTURE", discovery_source=c.evidence_url, verification_status=result.band, verification_provider=result.provider, catch_all=result.catch_all, bounce_risk=result.band, verified_at=now())
                    session.add(email); session.flush()
                reason = c.rejection_reason
                if icp < brief.qualification.minimum_icp_score:
                    status, reason = PipelineState.COMPANY_REJECTED, reason or "ICP score below threshold"
                elif buyer < brief.qualification.minimum_buyer_score:
                    status, reason = PipelineState.PERSON_REJECTED, reason or "Buyer score below threshold"
                elif is_suppressed(session, address, domain):
                    status, reason = PipelineState.SUPPRESSED, "Email or domain is suppressed"
                else:
                    confidence, band, status = confidence_gate(result, brief.qualification.minimum_confidence_score)
                    if status == PipelineState.REJECTED:
                        reason = reason or result.reason
                confidence = float(result.provider_score)
                prospect = Prospect(company_id=company.id, person_id=person.id, email_id=email.id if email else None, icp_score=icp, buyer_score=buyer, confidence_score=confidence, confidence_band=result.band, need_hypothesis="MOCK FIXTURE: operational growth may create process complexity.", personalization_angle="MOCK FIXTURE: reference the public operational hiring signal.", rejection_reason=reason, status=status)
                session.add(prospect); session.flush()
                session.add(Evidence(entity_type="prospect", entity_id=prospect.id, evidence_type="fixture", source_url=c.evidence_url, source_title="MOCK FIXTURE evidence", excerpt=f"MOCK FIXTURE for {c.company_name}; not real-world evidence."))
                results.append(prospect)
            job.status, job.completed_at, job.processed_count = "COMPLETED", now(), len(results)
            job.accepted_count = sum(p.status == PipelineState.READY_FOR_REVIEW for p in results)
            job.rejected_count = len(results) - job.accepted_count
            session.commit()
            log.info(json.dumps({"event": "mock_pipeline_completed", "processed": job.processed_count, "accepted": job.accepted_count}))
            return list(session.scalars(select(Prospect).order_by(Prospect.id)))

