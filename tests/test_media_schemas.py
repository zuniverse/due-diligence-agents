from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from kyc.domain.media import (
    AdverseMediaReport,
    AdverseMediaRequest,
    Confidence,
    IdentityMatch,
    MediaCategory,
    MediaResearchOutput,
    MediaSignal,
    ReportStatus,
    SearchTrace,
    SourceRef,
    SubjectKind,
)
from kyc.domain.schemas import RiskLevel

REQUEST = AdverseMediaRequest(
    case_id="case-1",
    subject="Viktor Orlenko",
    kind=SubjectKind.PERSON,
    company="Atelier Lumière SAS",
    roles=["bénéficiaire effectif"],
)
SOURCE = SourceRef(
    id="a-001",
    title="Un négociant marseillais mis en examen",
    publisher="Le Courrier du Port",
    published_at=date(2025, 3, 14),
)
SIGNAL = MediaSignal(
    category=MediaCategory.FRAUD,
    summary="Mis en examen pour escroquerie en bande organisée.",
    severity=RiskLevel.HIGH,
    identity_match=IdentityMatch.STRONG,
    source_ids=["a-001"],
)


def trace(**overrides) -> SearchTrace:
    values = {
        "backend": "local:test",
        "model": "fake",
        "queries": ["Viktor Orlenko"],
        "opened": ["a-001"],
        "started_at": datetime(2026, 10, 13, tzinfo=UTC),
        "duration_ms": 1200,
    }
    return SearchTrace(**(values | overrides))


def report(**overrides) -> AdverseMediaReport:
    values = {
        "request": REQUEST,
        "status": ReportStatus.OK,
        "signals": [SIGNAL],
        "sources": [SOURCE],
        "confidence": Confidence.HIGH,
        "trace": trace(),
    }
    return AdverseMediaReport(**(values | overrides))


def test_report_survives_checkpoint_round_trip():
    original = report()
    stored = original.model_dump(mode="json")
    assert AdverseMediaReport.model_validate(stored) == original


def test_model_can_only_fill_signals():
    schema = MediaResearchOutput.model_json_schema()
    assert set(schema["properties"]) == {"signals"}


def test_unknown_field_from_model_is_rejected():
    with pytest.raises(ValidationError):
        MediaResearchOutput.model_validate({"signals": [], "confidence": "high"})


def test_signal_without_source_is_rejected():
    with pytest.raises(ValidationError):
        MediaSignal(**(SIGNAL.model_dump() | {"source_ids": []}))


def test_no_hits_report_is_valid():
    r = report(
        status=ReportStatus.NO_HITS,
        signals=[],
        sources=[],
        confidence=Confidence.MEDIUM,
    )
    assert r.status == ReportStatus.NO_HITS


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"signals": [], "sources": []}, "statut ok sans signal"),
        ({"status": ReportStatus.NO_HITS}, "statut no_hits avec des signaux"),
        (
            {
                "status": ReportStatus.FAILED,
                "signals": [],
                "sources": [],
                "confidence": Confidence.LOW,
            },
            "failed sans erreur",
        ),
        (
            {
                "status": ReportStatus.FAILED,
                "signals": [],
                "sources": [],
                "trace": trace(error="timeout"),
            },
            "failed avec une confiance non faible",
        ),
        ({"sources": []}, "citées mais non décrites"),
        ({"trace": trace(opened=[])}, "jamais consultées"),
    ],
)
def test_inconsistent_report_is_rejected(overrides, reason):
    with pytest.raises(ValidationError, match=reason):
        report(**overrides)
