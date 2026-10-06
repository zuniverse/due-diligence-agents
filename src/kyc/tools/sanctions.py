import json
from pathlib import Path

from langchain_core.tools import tool

DATA = Path(__file__).resolve().parents[3] / "data"


@tool
def screen_sanctions(name: str) -> dict:
    """Vérifie si une personne figure sur la liste de sanctions."""
    sanctions = json.loads((DATA / "sanctions.json").read_text())
    hit = any(name.strip().lower() == s.lower() for s in sanctions)
    return {"name": name, "match": hit}
