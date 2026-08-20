import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from emailfinder.domain.phase2 import PublicEvidence, QualificationOutput, SourceQuality
from emailfinder.persistence.database import Company, Evidence, Job, Prospect, now, normalize_domain
from emailfinder.providers.evidence import identity_confirmed
from emailfinder.services.qualification import final_icp_score, hard_rejection
from emailfinder.services.facts import deterministic_score, resolve_facts, resolved_hard_rejection

log = logging.getLogger(__name__)


class Phase2APipeline:
    def __init__(self, engine, discovery, evidence_provider, reasoning):
        self.engine, self.discovery, self.evidence_provider, self.reasoning = engine, discovery, evidence_provider, reasoning

    def run(self, brief):
        with Session(self.engine, expire_on_commit=False) as session:
            job = Job(job_type="REAL_PHASE_2A", status="RUNNING")
            session.add(job); session.commit()
            try:
                discovered = self.discovery.discover(brief)
                candidates = [candidate for candidate in discovered if candidate.entity_resolution_status == "CONFIRMED"]
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
                    elif candidate.discovery_excerpt.startswith("SEARCH_DISCOVERY:"):
                        public_evidence.insert(0, PublicEvidence(id="search-1", evidence_type="search_discovery", source_url=candidate.discovery_url, source_title=candidate.discovery_title, excerpt=candidate.discovery_excerpt, source_quality=SourceQuality.SEARCH_DISCOVERY))
                    for item in public_evidence:
                        session.add(Evidence(entity_type="company", entity_id=company.id, evidence_type=item.evidence_type, source_url=str(item.source_url), source_title=item.source_title, excerpt=item.excerpt, source_quality=item.source_quality))
                    job.evidence_count += len(public_evidence)
                    # Discovery-only snippets may explain why an entity entered the
                    # pipeline, but must never contribute company facts or reach the
                    # qualifier as target-company evidence.
                    company_evidence = [item for item in public_evidence if item.source_quality != SourceQuality.SEARCH_DISCOVERY]
                    facts = resolve_facts(company_evidence, brief)
                    rejection = resolved_hard_rejection(facts, brief) or hard_rejection(candidate, company_evidence, brief)
                    if not identity_confirmed(candidate, company_evidence):
                        rejection = "INSUFFICIENT_EVIDENCE: company identity not established on its public website"
                    if rejection:
                        qualification = "INSUFFICIENT_EVIDENCE" if rejection.startswith("INSUFFICIENT") else "REJECT"
                        output = None; score = confidence = model_score = 0; det_score = deterministic_score(facts, brief); reason = rejection; need = None
                    else:
                        output = self.reasoning.qualify_company(candidate, company_evidence, brief, facts)
                        det_score, model_score = deterministic_score(facts, brief), output.model_score
                        score = final_icp_score(det_score, model_score)
                        qualification = output.qualification
                        if qualification == "ACCEPT" and score < brief.qualification.minimum_icp_score: qualification = "REJECT"
                        reason, need, confidence = f"Final ICP score {score}/100 (model score {model_score}/100; deterministic factual score {det_score}/55). {output.reason}", output.need_hypothesis if qualification == "ACCEPT" else None, output.confidence
                        job.evaluated_count += 1
                    company.status = qualification
                    session.add(Prospect(company_id=company.id, person_id=None, email_id=None, icp_score=score, deterministic_score=det_score, model_score=model_score, final_icp_score=score, buyer_score=0, confidence_score=confidence, confidence_band=qualification, need_hypothesis=need, personalization_angle=None, rejection_reason=reason if qualification != "ACCEPT" else None, status=qualification))
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
