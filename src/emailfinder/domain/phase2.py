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


class DiscoveredCompany(BaseModel):
    name: str = Field(min_length=2)
    domain: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]+\.[a-z]{2,}$")
    website: HttpUrl
    discovery_url: HttpUrl
    discovery_title: str
    discovery_excerpt: str


class PublicEvidence(BaseModel):
    id: str
    evidence_type: str
    source_url: HttpUrl
    source_title: str
    excerpt: str = Field(min_length=20, max_length=4000)
    source_quality: SourceQuality


class QualificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str
    domain: str
    industry_assessment: str
    geography_assessment: str
    size_assessment: str
    positive_signals: list[str]
    negative_signals: list[str]
    industry_fit: int = Field(ge=0, le=100)
    company_size_fit: int = Field(ge=0, le=100)
    geography_fit: int = Field(ge=0, le=100)
    workflow_signals: int = Field(ge=0, le=100)
    exclusion_risk: int = Field(ge=0, le=100)
    icp_score: int = Field(ge=0, le=100)
    qualification: Literal["ACCEPT", "REJECT", "INSUFFICIENT_EVIDENCE"]
    reason: str
    need_hypothesis: str
    evidence_ids_used: list[str]
    confidence: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def accepted_has_evidence(self):
        if not self.evidence_ids_used:
            raise ValueError("qualification requires at least one supplied evidence ID")
        if self.qualification == "ACCEPT" and not self.need_hypothesis.strip():
            raise ValueError("accepted qualification requires evidence and a need hypothesis")
        if self.qualification != "ACCEPT" and self.need_hypothesis.strip():
            raise ValueError("non-accepted qualification must not include a need hypothesis")
        return self
