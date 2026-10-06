from enum import Enum

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    category: str = Field(description="coherence, sanctions, structure ou autre")
    detail: str
    severity: RiskLevel


class KycAssessment(BaseModel):
    risk_level: RiskLevel
    score: int = Field(ge=0, le=100)
    findings: list[Finding]
    requires_human_review: bool
    rationale: str
