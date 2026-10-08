from pydantic import BaseModel

from kyc.domain.schemas import Finding, RiskLevel

SANCTION_FLOOR = 90  # une sanction confirmée impose au moins ce score
UNKNOWN_SIREN = 50
ADDRESS_MISMATCH = 30
UNKNOWN_DIRECTOR = 25
OWNERSHIP_GAP = 20

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 30


class Verdict(BaseModel):
    findings: list[Finding]
    score: int
    risk_level: RiskLevel
    requires_human_review: bool


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def evaluate(dossier: dict, registry: dict, sanctions: dict[str, bool]) -> Verdict:
    findings: list[Finding] = []
    score = 0

    sanctioned = sorted(name for name, hit in sanctions.items() if hit)
    for name in sanctioned:
        findings.append(
            Finding(
                category="sanctions",
                detail=f"{name} figure sur la liste de sanctions",
                severity=RiskLevel.HIGH,
            )
        )

    if "erreur" in registry:
        findings.append(
            Finding(
                category="coherence",
                detail=f"SIREN {dossier.get('siren')} introuvable au registre",
                severity=RiskLevel.HIGH,
            )
        )
        score += UNKNOWN_SIREN
    else:
        declared = dossier.get("adresse_siege")
        official = registry.get("adresse_siege")
        if _norm(declared) != _norm(official):
            findings.append(
                Finding(
                    category="coherence",
                    detail=(
                        f"Adresse déclarée '{declared}' différente "
                        f"du registre '{official}'"
                    ),
                    severity=RiskLevel.MEDIUM,
                )
            )
            score += ADDRESS_MISMATCH

        unknown = sorted(
            set(dossier.get("dirigeants", [])) - set(registry.get("dirigeants", []))
        )
        if unknown:
            findings.append(
                Finding(
                    category="coherence",
                    detail=f"Dirigeants absents du registre : {unknown}",
                    severity=RiskLevel.MEDIUM,
                )
            )
            score += UNKNOWN_DIRECTOR

    total = sum(b.get("part", 0) for b in dossier.get("beneficiaires_effectifs", []))
    if total != 100:
        findings.append(
            Finding(
                category="structure",
                detail=f"Les parts des bénéficiaires effectifs totalisent {total} %",
                severity=RiskLevel.MEDIUM,
            )
        )
        score += OWNERSHIP_GAP

    if sanctioned:
        score = max(score, SANCTION_FLOOR)
    score = min(score, 100)

    if score >= HIGH_THRESHOLD:
        level = RiskLevel.HIGH
    elif score >= MEDIUM_THRESHOLD:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    return Verdict(
        findings=findings,
        score=score,
        risk_level=level,
        requires_human_review=level != RiskLevel.LOW,
    )
