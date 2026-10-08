from kyc.domain.schemas import RiskLevel
from kyc.domain.scoring import evaluate

REGISTRY = {
    "adresse_siege": "12 rue des Orfèvres, 75003 Paris",
    "dirigeants": ["Camille Berthier"],
}


def test_clean_case_is_low_risk():
    dossier = {
        "siren": "123456789",
        "adresse_siege": "12 rue des Orfèvres, 75003 Paris",
        "dirigeants": ["Camille Berthier"],
        "beneficiaires_effectifs": [{"nom": "Camille Berthier", "part": 100}],
    }
    v = evaluate(dossier, REGISTRY, {"Camille Berthier": False})
    assert v.score == 0 and v.risk_level == RiskLevel.LOW
    assert not v.requires_human_review


def test_sanction_forces_high_risk():
    dossier = {
        "siren": "123456789",
        "adresse_siege": "12 rue des Orfèvres, 75003 Paris",
        "dirigeants": ["Camille Berthier"],
        "beneficiaires_effectifs": [{"nom": "Viktor Orlenko", "part": 100}],
    }
    v = evaluate(dossier, REGISTRY, {"Viktor Orlenko": True})
    assert v.score >= 90 and v.risk_level == RiskLevel.HIGH
    assert v.requires_human_review
