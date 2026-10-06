import json
from pathlib import Path

from langchain_core.tools import tool

DATA = Path(__file__).resolve().parents[3] / "data"


@tool
def get_company_record(siren: str) -> dict:
    """Retourne la fiche officielle d'une société à partir de son SIREN."""
    registre = json.loads((DATA / "registre.json").read_text())
    return registre.get(siren, {"erreur": "SIREN introuvable"})
