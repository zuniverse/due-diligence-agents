import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from kyc.domain.prompts import NARRATIVE_SYSTEM
from kyc.domain.schemas import KycAssessment, Narrative, ReviewDecision
from kyc.domain.scoring import CHECKS_PERFORMED, Verdict, evaluate
from kyc.tools import get_company_record, screen_sanctions

# imports et config
KYC_MODEL: str = os.getenv("KYC_MODEL", "qwen3:4b")
llm = ChatOllama(model=KYC_MODEL, temperature=0, keep_alive="15m", reasoning=False)


class CaseState(TypedDict):
    """l’état, la caisse partagée"""

    dossier: dict
    registry: dict
    sanctions: dict[str, bool]
    verdict: dict
    assessment: dict
    decision: dict


def fetch_registry(state: CaseState) -> dict:
    """Poste 1, fetch_registry : va chercher la fiche officielle"""
    record = get_company_record.invoke({"siren": state["dossier"]["siren"]})
    return {"registry": record}


def screen_people(state: CaseState) -> dict:
    """Poste 2, screen_people : vérifie chaque personne contre la liste de sanctions"""
    dossier = state["dossier"]
    names = set(dossier.get("dirigeants", []))
    names |= {b["nom"] for b in dossier.get("beneficiaires_effectifs", [])}
    hits = {n: screen_sanctions.invoke({"name": n})["match"] for n in sorted(names)}
    return {"sanctions": hits}


def evaluate_rules(state: CaseState) -> dict:
    """Poste 3, evaluate_rules : appelle la fonction evaluate de scoring.py, qui applique
    les règles (adresse différente, sanction, etc.) et calcule le score. Le résultat
    est rangé dans verdict"""
    verdict = evaluate(state["dossier"], state["registry"], state["sanctions"])
    return {"verdict": verdict.model_dump(mode="json")}


def narrate(state: CaseState) -> dict:
    """Poste 4, narrate : le seul poste qui appelle le modèle"""
    if os.getenv("KYC_FAIL_AT") == "narrate":
        raise RuntimeError("Crash simulé avant la rédaction")
    verdict = Verdict.model_validate(state["verdict"])
    facts = json.dumps(
        {
            "societe": state["dossier"].get("denomination"),
            "controles_effectues": list(CHECKS_PERFORMED),
            "constats": [
                {"categorie": f.category, "gravite": f.severity.value}
                for f in verdict.findings
            ],
            "score": verdict.score,
            "niveau_de_risque": verdict.risk_level.value,
        },
        ensure_ascii=False,
        indent=2,
    )
    structured = llm.with_structured_output(Narrative, method="json_schema")
    narrative = Narrative.model_validate(
        structured.invoke([SystemMessage(NARRATIVE_SYSTEM), HumanMessage(facts)])
    )
    assessment = KycAssessment(**verdict.model_dump(), rationale=narrative.rationale)
    return {"assessment": assessment.model_dump(mode="json")}


def human_review(state: CaseState) -> dict:
    """Poste 5, human_review : la décision humaine"""
    assessment = state["assessment"]
    if not assessment["requires_human_review"]:
        auto = ReviewDecision(
            decision="approved",
            reviewer="automatic",
            comment="Risque faible : validation automatique",
        )
        return {"decision": auto.model_dump(mode="json")}

    # Pause : l'exécution s'arrête ici jusqu'à une reprise avec Command(resume=...)
    answer = interrupt({"dossier": state["dossier"], "assessment": assessment})
    decision = ReviewDecision.model_validate(answer)
    return {"decision": decision.model_dump(mode="json")}


def build_graph(checkpointer: BaseCheckpointSaver, with_review: bool = True):
    """Partie 4 : build_graph, le plan de la chaîne"""
    g = StateGraph(CaseState)
    g.add_node("fetch_registry", fetch_registry)
    g.add_node("screen_people", screen_people)
    g.add_node("evaluate_rules", evaluate_rules)
    g.add_node("narrate", narrate)
    g.add_edge(START, "fetch_registry")
    g.add_edge("fetch_registry", "screen_people")
    g.add_edge("screen_people", "evaluate_rules")
    g.add_edge("evaluate_rules", "narrate")
    if with_review:
        g.add_node("human_review", human_review)
        g.add_edge("narrate", "human_review")
        g.add_edge("human_review", END)
    else:
        g.add_edge("narrate", END)
    return g.compile(checkpointer=checkpointer)


# Partie 5 : les fonctions utilitaires


def open_graph(with_review: bool = True) -> tuple[Any, sqlite3.Connection]:
    """open_graph crée le dossier .checkpoints, ouvre la base SQLite et construit le graphe.
    Elle renvoie le graphe et la connexion"""
    Path(".checkpoints").mkdir(exist_ok=True)
    conn = sqlite3.connect(".checkpoints/cases.sqlite", check_same_thread=False)
    return build_graph(SqliteSaver(conn), with_review), conn


def pending_interrupt(graph: Any, config: RunnableConfig) -> Any | None:
    """pending_interrupt répond à la question “ce dossier est-il en pause, et que doit voir le
    relecteur ?”. Elle regarde les tâches de l’état courant, et si l’une contient une
    interruption, elle renvoie son contenu. Sinon, elle renvoie None

    Retourne ce que le relecteur doit voir si le dossier est en pause, sinon None."""
    for task in graph.get_state(config).tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def advance(graph: Any, config: RunnableConfig, payload: Any) -> None:
    """advance fait tourner le graphe et affiche la progression :"""
    for update in graph.stream(payload, config, stream_mode="updates"):
        for node in update:
            if node == "__interrupt__":
                print("[human_review] en attente de validation", file=sys.stderr)
            else:
                print(f"[{node}] terminé", file=sys.stderr)


def analyze(dossier: dict, thread_id: str) -> dict:
    """Instruction sans revue humaine. Idempotente : relancée avec le même
    thread_id, elle reprend au checkpoint ou renvoie le résultat déjà calculé."""
    graph, conn = open_graph(with_review=False)
    try:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        if snapshot.values.get("assessment"):
            return snapshot.values["assessment"]
        payload: dict | None
        if snapshot.next:
            print(f"Reprise avant le noeud : {snapshot.next}", file=sys.stderr)
            payload = None
        else:
            payload = {"dossier": dossier}
        advance(graph, config, payload)
        return graph.get_state(config).values["assessment"]
    finally:
        conn.close()


# Partie 6 : run, le chef de chantier


def run(dossier_path: str, thread_id: str) -> tuple[dict, bool]:
    """Le thread_id est l’étiquette de la caisse : c’est avec elle qu’on retrouve les
    photos d’un dossier. run regarde où en est ce dossier

    Situation	          -->                     Que fait run
    Le dossier est en pause, en attente d’un humain --> Ne relance rien, signale l’attente
    Le dossier est à moitié traité (plantage) --> Envoie None, ce qui veut dire “reprends
    à la dernière photo”
    Le dossier est nouveau --> Envoie le contenu du fichier JSON comme point de départ"""
    graph, _ = open_graph()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)
    if pending_interrupt(graph, config) is not None:
        print("Dossier déjà en attente de validation humaine", file=sys.stderr)
    else:
        payload: dict | None
        if snapshot.next:
            print(f"Reprise avant le noeud : {snapshot.next}", file=sys.stderr)
            payload = None
        else:
            payload = {"dossier": json.loads(Path(dossier_path).read_text())}
        advance(graph, config, payload)

    paused = pending_interrupt(graph, config) is not None
    return graph.get_state(config).values, paused


if __name__ == "__main__":
    # En résumé, ce qui se passe quand tu lances la commande
    # Tu donnes un fichier de dossier et une étiquette.
    # run regarde si cette étiquette existe déjà dans SQLite (nouveau, en panne, ou en pause).
    # Le graphe exécute les postes dans l’ordre, et une photo est prise après chacun.
    # Les trois premiers postes sont du Python pur, le quatrième appelle le modèle uniquement
    # pour rédiger du texte.
    # Au cinquième, soit le dossier est approuvé automatiquement, soit tout s’arrête dans
    # interrupt jusqu’à ce qu’un humain réponde.
    path = sys.argv[1]
    thread = sys.argv[2] if len(sys.argv) > 2 else Path(path).stem
    values, paused = run(path, thread)
    print(json.dumps(values["assessment"], ensure_ascii=False, indent=2))
    if paused:
        print(
            f"\nEn attente de validation : "
            f"uv run python -m kyc.agent.review show {thread}",
            file=sys.stderr,
        )
    else:
        print(json.dumps(values["decision"], ensure_ascii=False, indent=2))
