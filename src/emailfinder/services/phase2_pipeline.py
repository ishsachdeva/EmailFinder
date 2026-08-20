import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from emailfinder.domain.phase2 import PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.persistence.database import Company, Evidence, Job, Prospect, now, normalize_domain
from emailfinder.providers.evidence import identity_confirmed
from emailfinder.services.qualification import hard_rejection, inspectable_score

log = logging.getLogger(__name__)


class Phase2APipeline:
    def __init__(self, engine, discovery, evidence_provider, reasoning):
        self.engine, self.discovery, self.evidence_provider, self.reasoning = engine, discovery, evidence_provider, reasoning

    def run(self, brief):
        with Session(self.engine, expire_on_commit=False) as session:
            job = Job(job_type="REAL_PHASE_2A", status="RUNNING")
            session.add(job); session.commit()
            try:
                candidates = self.discovery.discover(brief)
            except Exception:
                job.status, job.error_count, job.completed_at = "FAILED", 1, now(); session.commit(); raise
            job.discovered_count = len(candidates)
            seen = set()
            for candidate in candidates:
                domain = normalize_domain(candidate.domain)
                if domain in seen: continue
                seen.add(domain)
                company = session.scalar(select(Company).where(Company.domain == domain))
                if company and company.status in {"ACCEPT", "REJECT", "INSUFFICIENT_EVIDENCE"}:
                    continue
                if not company:
                    company = Company(name=candidate.name, domain=domain, website=str(candidate.website), industry=None, employee_range=None, country=None, status="DISCOVERED")
                    session.add(company); session.flush()
                try:
                    public_evidence = self.evidence_provider.collect(candidate)
                    # Wikidata discovery fields are structured CC0 claims with a
                    # traceable entity URL. Retain them for gates and reasoning;
                    # do not promote ordinary search snippets to evidence.
                    if urlparse(str(candidate.discovery_url)).netloc.endswith("wikidata.org") and len(candidate.discovery_excerpt) >= 20:
                        public_evidence.insert(0, PublicEvidence(id="wikidata-1", evidence_type="public_business_directory", source_url=candidate.discovery_url, source_title=candidate.discovery_title, excerpt=candidate.discovery_excerpt, source_quality=SourceQuality.REPUTABLE_SECONDARY))
                    for item in public_evidence:
                        session.add(Evidence(entity_type="company", entity_id=company.id, evidence_type=item.evidence_type, source_url=str(item.source_url), source_title=item.source_title, excerpt=item.excerpt, source_quality=item.source_quality))
                    job.evidence_count += len(public_evidence)
                    rejection = hard_rejection(candidate, public_evidence, brief)
                    if not identity_confirmed(candidate, public_evidence):
                        rejection = "INSUFFICIENT_EVIDENCE: company identity not established on its public website"
                    if rejection:
                        qualification = "INSUFFICIENT_EVIDENCE" if rejection.startswith("INSUFFICIENT") else "REJECT"
                        output = None; score = confidence = 0; reason = rejection; need = ""
                    else:
                        output = self.reasoning.qualify_company(candidate, public_evidence, brief)
                        score = inspectable_score(output, brief)
                        qualification = output.qualification
                        if qualification == "ACCEPT" and score < brief.qualification.minimum_icp_score: qualification = "REJECT"
                        reason, need, confidence = output.reason, output.need_hypothesis if qualification == "ACCEPT" else "", output.confidence
                        company.industry = output.industry_assessment[:150]
                        company.country = output.geography_assessment[:100]
                        company.employee_range = output.size_assessment[:50]
                        job.evaluated_count += 1
                    company.status = qualification
                    session.add(Prospect(company_id=company.id, person_id=None, email_id=None, icp_score=score, buyer_score=0, confidence_score=confidence, confidence_band=qualification, need_hypothesis=need, personalization_angle=None, rejection_reason=reason if qualification != "ACCEPT" else None, status=qualification))
                    if qualification == "ACCEPT": job.accepted_count += 1
                    elif qualification == "INSUFFICIENT_EVIDENCE": job.insufficient_count += 1
                    else: job.rejected_count += 1
                except Exception as exc:
                    job.error_count += 1
                    company.status = "ERROR"
                    log.warning("company_processing_failed domain=%s error_type=%s category=%s", domain, type(exc).__name__, getattr(exc, "category", "UNCLASSIFIED"))
                job.processed_count += 1
                session.commit()
            job.status, job.completed_at = "COMPLETED", now(); session.commit()
            return job
