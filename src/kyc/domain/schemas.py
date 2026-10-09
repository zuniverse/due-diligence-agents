from datetime import UTC, datetime
from enum import Enum
from typing import Literal

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


class Narrative(BaseModel):
    rationale: str = Field(description="Justification en 3 à 4 phrases, en français")


class ReviewDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str
    comment: str = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
