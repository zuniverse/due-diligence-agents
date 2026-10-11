from kyc.domain.narrative import clean, fallback, finalize
from kyc.domain.scoring import Verdict

VERDICT = Verdict.model_validate(
    {
        "findings": [
            {"category": "sanctions", "detail": "x", "severity": "high"},
            {"category": "coherence", "detail": "y", "severity": "medium"},
        ],
        "score": 90,
        "risk_level": "high",
        "requires_human_review": True,
    }
)

GOOD = (
    "Les contrôles ont relevé deux constats, dont un de gravité élevée. "
    "Le score de 90 correspond à un niveau de risque élevé. "
    "Une relecture humaine est nécessaire."
)


def test_valid_text_is_kept():
    text, used_fallback = finalize(GOOD, VERDICT)
    assert text == GOOD and not used_fallback


def test_think_block_is_removed():
    assert clean("<think>je réfléchis</think> " + GOOD) == GOOD


def test_leaked_reasoning_uses_fallback():
    raw = "L'utilisateur demande de rédiger une justification. Je dois éviter les détails."
    text, used_fallback = finalize(raw, VERDICT)
    assert used_fallback and "L'utilisateur" not in text


def test_truncated_text_uses_fallback():
    raw = GOOD[:-20]
    _, used_fallback = finalize(raw, VERDICT)
    assert used_fallback


def test_empty_text_uses_fallback():
    _, used_fallback = finalize("", VERDICT)
    assert used_fallback


def test_fallback_mentions_score_and_categories():
    text = fallback(VERDICT)
    assert "90" in text and "sanctions" in text and "coherence" in text
