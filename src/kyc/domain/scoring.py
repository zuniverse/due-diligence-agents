"""Règles de scoring KYC/KYB.

Module pur : aucun appel à un modèle de langage, aucune lecture de fichier.
À données identiques, le résultat est toujours identique, ce qui le rend
testable sans Ollama et auditable par la conformité.

Les valeurs ci-dessous sont des exemples de démonstration, à fixer avec la
conformité avant tout usage réel.
"""

from pydantic import BaseModel

from kyc.domain.schemas import Finding, RiskLevel

SANCTION_FLOOR = 90  # score minimal dès qu'une personne est sur la liste de sanctions
UNKNOWN_SIREN = 50  # SIREN absent du registre
ADDRESS_MISMATCH = 30  # adresse du siège différente de la fiche officielle
UNKNOWN_DIRECTOR = 25  # dirigeant déclaré absent de la fiche officielle
OWNERSHIP_GAP = 20  # parts des bénéficiaires effectifs différentes de 100 %

HIGH_THRESHOLD = 70  # score >= 70 : risque élevé
MEDIUM_THRESHOLD = 30  # score >= 30 : risque moyen, sinon faible

# Contrôles réellement effectués par evaluate(), communiqués au modèle pour qu'il
# n'affirme rien au-delà.
CHECKS_PERFORMED = (
    "registre",
    "adresse du siège",
    "dirigeants",
    "sanctions",
    "répartition du capital",
)


class Verdict(BaseModel):
    """Résultat des règles de scoring pour un dossier.

    Attributes:
        findings: constats établis, avec leur catégorie, leur détail et leur gravité.
        score: score de risque entre 0 et 100.
        risk_level: niveau de risque déduit du score (faible, moyen, élevé).
        requires_human_review: True dès que le niveau de risque n'est pas faible.
    """

    findings: list[Finding]
    score: int
    risk_level: RiskLevel
    requires_human_review: bool


def _norm(text: str | None) -> str:
    """Normalise un texte pour une comparaison tolérante.

    Passe en minuscules (casefold), réduit les espaces multiples à un seul et
    traite None comme une chaîne vide.

    Args:
        text: texte à normaliser, éventuellement absent.

    Returns:
        Le texte normalisé, jamais None.
    """
    return " ".join((text or "").casefold().split())


def evaluate(dossier: dict, registry: dict, sanctions: dict[str, bool]) -> Verdict:
    """Applique les règles de scoring à un dossier et renvoie le verdict.

    Règles appliquées, dans cet ordre :

    1. Chaque personne dont le résultat de screening est True produit un constat
       de sanction de gravité élevée.
    2. Si le SIREN est introuvable au registre : constat grave et UNKNOWN_SIREN.
       Sinon, comparaison à la fiche officielle de l'adresse du siège
       (ADDRESS_MISMATCH) et des dirigeants (UNKNOWN_DIRECTOR).
    3. Si les parts des bénéficiaires effectifs ne totalisent pas 100 : OWNERSHIP_GAP.
    4. Une sanction relève le score à SANCTION_FLOOR au minimum, puis le score est
       plafonné à 100.
    5. Le niveau de risque se déduit du score avec HIGH_THRESHOLD et
       MEDIUM_THRESHOLD, et toute revue non faible demande un humain.

    Args:
        dossier: dossier déclaré par le client (siren, adresse_siege, dirigeants,
            beneficiaires_effectifs avec leur part en pourcentage).
        registry: fiche officielle de la société. Un dict contenant la clé "erreur"
            signale un SIREN introuvable.
        sanctions: résultat du screening, nom de la personne vers True si elle figure
            sur la liste de sanctions.

    Returns:
        Un Verdict avec les constats, le score, le niveau de risque et le drapeau de
        revue humaine.
    """
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
