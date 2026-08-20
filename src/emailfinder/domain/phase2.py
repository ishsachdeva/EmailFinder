from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SourceQuality(StrEnum):
    PRIMARY = "PRIMARY"
    REPUTABLE_SECONDARY = "REPUTABLE_SECONDARY"
    WEAK_SECONDARY = "WEAK_SECONDARY"
    SEARCH_DISCOVERY = "SEARCH_DISCOVERY"


class ResolvedFact(BaseModel):
    value: str | int | None = None
    evidence_ids: list[str] = []
    confidence: int = Field(default=0, ge=0, le=100)
    conflict: bool = False
    alternatives: list[str] = []


class ResolvedFacts(BaseModel):
    geography: ResolvedFact = Field(default_factory=ResolvedFact)
    employee_count: ResolvedFact = Field(default_factory=ResolvedFact)
    industry: ResolvedFact = Field(default_factory=ResolvedFact)
    operating_status: ResolvedFact = Field(default_factory=lambda: ResolvedFact(value="UNKNOWN"))


class DiscoveryLane(StrEnum):
    JOB_TRIGGER = "JOB_TRIGGER"
    OFFICIAL_COMPANY_SIGNAL = "OFFICIAL_COMPANY_SIGNAL"


class CandidateSeed(BaseModel):
    company_name: str
    official_domain: str
    discovery_lane: DiscoveryLane
    trigger_name: str
    trigger_tier: Literal["A"] = "A"
    trigger_strength: int = Field(ge=1, le=100)
    trigger_source_url: HttpUrl
    trigger_source_type: str
    trigger_excerpt: str = Field(min_length=5, max_length=1500)
    trigger_date: str | None = None
    entity_confidence: int = Field(ge=0, le=100)
    query_used: str
    seed_quality_score: int = Field(ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiscoveredCompany(BaseModel):
    name: str = Field(min_length=2)
    domain: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]+\.[a-z]{2,}$")
    website: HttpUrl
    discovery_url: HttpUrl
    discovery_title: str
    discovery_excerpt: str
    discovery_source_type: str = "UNKNOWN"
    resolution_source: str | None = None
    resolution_confidence: int = Field(default=0, ge=0, le=100)
    domain_validation_status: str = "UNVALIDATED"
    entity_resolution_status: Literal["CONFIRMED", "PROBABLE", "UNRESOLVED", "REJECTED"] = "UNRESOLVED"
    candidate_seed: CandidateSeed | None = None


class PublicEvidence(BaseModel):
    id: str
    evidence_type: str
    source_url: HttpUrl
    source_title: str
    excerpt: str = Field(min_length=20, max_length=4000)
    source_quality: SourceQuality


class ModelSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str = Field(min_length=2, max_length=300)
    evidence_ids: list[str] = Field(min_length=1)


class QualificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualification: Literal["ACCEPT", "REJECT", "INSUFFICIENT_EVIDENCE"]
    model_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    positive_signals: list[ModelSignal]
    negative_signals: list[ModelSignal]
    reason: str
    need_hypothesis: str | None = None
    need_hypothesis_evidence_ids: list[str] = []

    @model_validator(mode="after")
    def valid_need_hypothesis(self):
        if self.qualification == "ACCEPT" and (not self.need_hypothesis or not self.need_hypothesis_evidence_ids):
            raise ValueError("accepted qualification requires an evidence-backed need hypothesis")
        if self.qualification != "ACCEPT" and (self.need_hypothesis is not None or self.need_hypothesis_evidence_ids):
            raise ValueError("non-accepted qualification must use null need_hypothesis and no need evidence")
        return self
