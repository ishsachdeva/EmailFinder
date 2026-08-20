from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator


Score = Annotated[int, Field(ge=0, le=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Company(StrictModel):
    name: str = Field(min_length=1)
    website: HttpUrl
    description: str = Field(min_length=1)
    offer: str = Field(min_length=1)


class ICP(StrictModel):
    target_industries: list[str]
    excluded_industries: list[str] = []
    employee_min: int = Field(ge=1)
    employee_max: int = Field(ge=1)
    target_geographies: list[str]
    excluded_geographies: list[str] = []

    @model_validator(mode="after")
    def valid_range(self):
        if self.employee_min > self.employee_max:
            raise ValueError("employee_min must not exceed employee_max")
        return self


class Buyers(StrictModel):
    primary_titles: list[str]
    secondary_titles: list[str] = []
    excluded_titles: list[str] = []


class Qualification(StrictModel):
    positive_signals: list[str] = []
    negative_signals: list[str] = []
    minimum_icp_score: Score
    minimum_buyer_score: Score
    minimum_confidence_score: Score = 70


class Personalization(StrictModel):
    allowed_evidence_types: list[str]
    prohibited_claims: list[str] = []


class Prospecting(StrictModel):
    daily_target: int = Field(gt=0)
    maximum_candidates_to_process: int = Field(gt=0)


class CompanyBrief(StrictModel):
    company: Company
    icp: ICP
    buyers: Buyers
    qualification: Qualification
    personalization: Personalization
    prospecting: Prospecting


class CompanyBriefError(ValueError):
    pass


def load_company_brief(path: str | Path) -> CompanyBrief:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CompanyBriefError("Company Brief must be a YAML mapping")
        return CompanyBrief.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError, CompanyBriefError) as exc:
        raise CompanyBriefError(f"Invalid Company Brief: {exc}") from exc

