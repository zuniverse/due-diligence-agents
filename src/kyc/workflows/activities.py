import json
import sys
from pathlib import Path

from temporalio import activity

from kyc.agent.graph import analyze

AUDIT_DIR = Path(".audit")


@activity.defn
def analyze_case(dossier: dict, thread_id: str) -> dict:
    """Instruit le dossier de bout en bout, sans pause de relecture.

    Rejouable : le checkpoint LangGraph (thread_id = case_id) reprend au noeud
    interrompu, et un dossier déjà instruit renvoie son résultat sans être
    relancé. La relecture humaine est gérée par le workflow, pas par le graphe.
    """
    return analyze(dossier, thread_id)


@activity.defn
def notify_missing_documents(workflow_id: str, missing: list[str]) -> None:
    """Signale à l'opérateur les pièces manquantes du dossier.

    Rejouable : Temporal peut relancer l'activité après un échec ou un rappel
    planifié. Une notification en double est sans danger, elle ne modifie
    aucun état du dossier.
    """
    # Emplacement d'un e-mail ou d'un message au client
    print(
        f"[notification] {workflow_id} : pièces manquantes {missing}", file=sys.stderr
    )


@activity.defn
def notify_reviewer(workflow_id: str, assessment: dict) -> None:
    """Prévient le relecteur qu'un dossier attend sa décision.

    Reçoit l'évaluation déjà calculée (score, niveau de risque, justification).
    Rejouable : un rappel envoyé deux fois est un défaut mineur, pas une
    erreur de données. Le suivi des rappels est tenu par le workflow.
    """
    # Emplacement de la création d'un ticket de revue
    print(
        f"[notification] {workflow_id} : revue requise (score={assessment['score']})",
        file=sys.stderr,
    )


@activity.defn
def record_decision(workflow_id: str, assessment: dict, decision: dict) -> None:
    """Écrit la décision finale dans la piste d'audit (.audit/<id>.json).

    Enregistre l'évaluation, la décision humaine (ou son absence, en cas
    d'abandon ou de délai dépassé) et l'horodatage. L'identifiant du dossier
    est nettoyé avant de servir de nom de fichier, pour éviter toute sortie
    du dossier d'audit.

    Rejouable : le fichier est réécrit avec le même contenu, donc un second
    appel donne le même résultat.
    """
    safe_id = "".join(c for c in workflow_id if c.isalnum() or c in "-_")
    AUDIT_DIR.mkdir(exist_ok=True)
    record = {"case_id": workflow_id, "assessment": assessment, "decision": decision}
    (AUDIT_DIR / f"{safe_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2)
    )
