"""Contrôle de la justification rédigée par le modèle.

Le modèle ne décide de rien, mais son texte arrive devant un relecteur humain.
Ce module nettoie le texte, le valide, et le remplace par un texte fabriqué par
le code quand il est inutilisable (raisonnement interne, texte coupé, vide).
"""

import re

from kyc.domain.scoring import CHECKS_PERFORMED, Verdict

MIN_CHARS = 60
MAX_CHARS = 700
MIN_SENTENCES = 2
MAX_SENTENCES = 6

# Formules qui trahissent un raisonnement interne au lieu d'une justification.
LEAK_MARKERS = (
    "l'utilisateur",
    "je dois",
    "je vais",
    "the user",
    "<think>",
    "```",
)

SEVERITY_FR = {"low": "faible", "medium": "moyenne", "high": "élevée"}
LEVEL_FR = {"low": "faible", "medium": "moyen", "high": "élevé"}


def clean(text: str) -> str:
    """Retire les blocs de réflexion éventuels et les espaces superflus."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return " ".join(text.split())


def is_valid(text: str) -> bool:
    """Vrai si le texte ressemble à une justification terminée et propre."""
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return False
    if text[-1] not in ".!?":
        return False
    lowered = text.casefold().replace("\u2019", "'")
    if any(marker in lowered for marker in LEAK_MARKERS):
        return False
    sentences = len(re.findall(r"[.!?](?:\s|$)", text))
    return MIN_SENTENCES <= sentences <= MAX_SENTENCES


def fallback(verdict: Verdict) -> str:
    """Justification fabriquée uniquement à partir du verdict, sans modèle."""
    level = LEVEL_FR[verdict.risk_level.value]
    if not verdict.findings:
        return (
            f"Aucun écart n'a été relevé sur les contrôles effectués "
            f"({', '.join(CHECKS_PERFORMED)}). "
            f"Score de {verdict.score} sur 100, niveau de risque {level}."
        )
    constats = ", ".join(
        f"{f.category} (gravité {SEVERITY_FR[f.severity.value]})"
        for f in verdict.findings
    )
    review = (
        " Une relecture humaine est requise." if verdict.requires_human_review else ""
    )
    return (
        f"Score de {verdict.score} sur 100, niveau de risque {level}. "
        f"Constats relevés : {constats}.{review}"
    )


def finalize(raw: str, verdict: Verdict) -> tuple[str, bool]:
    """Renvoie (justification, repli_utilisé)."""
    text = clean(raw)
    if is_valid(text):
        return text, False
    return fallback(verdict), True
