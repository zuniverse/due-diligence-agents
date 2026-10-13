"""Contrat d'échange entre l'agent d'instruction et l'agent de presse défavorable.

L'agent d'instruction envoie une AdverseMediaRequest par sujet et reçoit un
AdverseMediaReport. Les deux sont validés de chaque côté et enregistrés dans le
checkpoint : les deux agents n'échangent jamais de texte libre.

Répartition des rôles :
- le modèle ne remplit que MediaResearchOutput, c'est-à-dire les signaux ;
- le code remplit tout le reste : statut, sources, confiance et trace.
"""

from datetime import date, datetime
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kyc.domain.schemas import RiskLevel


class _Strict(BaseModel):
    """Refuse tout champ inconnu, qu'il vienne du modèle ou du code."""

    model_config = ConfigDict(extra="forbid")


class SubjectKind(str, Enum):
    PERSON = "person"
    COMPANY = "company"


class AdverseMediaRequest(_Strict):
    """Demande de l'agent d'instruction pour un sujet."""

    case_id: str
    subject: str = Field(min_length=2, description="Nom de la personne ou dénomination")
    kind: SubjectKind
    company: str | None = Field(
        default=None, description="Société du dossier, pour écarter les homonymes"
    )
    roles: list[str] = Field(
        default_factory=list, description="Rôles dans le dossier, ex. dirigeant"
    )


class MediaCategory(str, Enum):
    FRAUD = "fraud"
    MONEY_LAUNDERING = "money_laundering"
    CORRUPTION = "corruption"
    LITIGATION = "litigation"
    OTHER = "other"


class IdentityMatch(str, Enum):
    STRONG = "strong"  # le nom et au moins un élément du dossier (société, rôle)
    WEAK = "weak"  # le nom seul


class MediaSignal(_Strict):
    """Un fait défavorable relevé dans la presse. Seul objet rempli par le modèle."""

    category: MediaCategory
    summary: str = Field(
        min_length=10,
        max_length=400,
        description="Le fait, en une ou deux phrases, en français",
    )
    severity: RiskLevel
    identity_match: IdentityMatch
    source_ids: list[str] = Field(
        min_length=1,
        description="Identifiants des articles lus qui établissent le fait",
    )


class MediaResearchOutput(_Strict):
    """Sortie structurée demandée au modèle : les signaux, rien d'autre."""

    signals: list[MediaSignal] = Field(default_factory=list)


class SourceRef(_Strict):
    """Un article, décrit par l'outil de recherche et non par le modèle."""

    id: str
    title: str
    publisher: str
    published_at: date
    url: str | None = None


class ReportStatus(str, Enum):
    OK = "ok"  # recherche menée, au moins un signal retenu
    NO_HITS = "no_hits"  # recherche menée, aucun signal retenu
    FAILED = "failed"  # recherche interrompue ou sortie inutilisable


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SearchTrace(_Strict):
    """Ce qui s'est réellement passé, enregistré par le code autour des outils."""

    backend: str = Field(description="Source interrogée et sa version")
    model: str
    queries: list[str] = Field(default_factory=list)
    opened: list[str] = Field(
        default_factory=list, description="Articles lus, dans l'ordre"
    )
    rejections: list[str] = Field(
        default_factory=list, description="Signaux écartés par le code, avec la raison"
    )
    error: str | None = None
    started_at: datetime
    duration_ms: int = Field(ge=0)


class AdverseMediaReport(_Strict):
    """Réponse de l'agent de presse pour un sujet.

    Les règles de cohérence ci-dessous font qu'un rapport incohérent ne peut pas
    exister : il est refusé dès sa construction ou sa relecture du checkpoint.
    """

    schema_version: Literal["1"] = "1"
    request: AdverseMediaRequest
    status: ReportStatus
    signals: list[MediaSignal] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence
    trace: SearchTrace

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.status == ReportStatus.OK and not self.signals:
            raise ValueError("statut ok sans signal")
        if self.status != ReportStatus.OK and self.signals:
            raise ValueError(f"statut {self.status.value} avec des signaux")
        if self.status == ReportStatus.FAILED:
            if self.trace.error is None:
                raise ValueError("statut failed sans erreur dans la trace")
            if self.confidence != Confidence.LOW:
                raise ValueError("statut failed avec une confiance non faible")

        described = {s.id for s in self.sources}
        cited = {i for signal in self.signals for i in signal.source_ids}
        if missing := cited - described:
            raise ValueError(f"sources citées mais non décrites : {sorted(missing)}")
        if unread := described - set(self.trace.opened):
            raise ValueError(f"sources jamais consultées : {sorted(unread)}")
        return self
