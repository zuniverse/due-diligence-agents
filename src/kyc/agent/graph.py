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
from kyc.domain.scoring import Verdict, evaluate
from kyc.tools import get_company_record, screen_sanctions

KYC_MODEL: str = os.getenv("KYC_MODEL", "qwen3:4b")
llm = ChatOllama(model=KYC_MODEL, temperature=0, keep_alive="15m", reasoning=False)


class CaseState(TypedDict):
    dossier: dict
    registry: dict
    sanctions: dict[str, bool]
    verdict: dict
    assessment: dict
    decision: dict


def fetch_registry(state: CaseState) -> dict:
    record = get_company_record.invoke({"siren": state["dossier"]["siren"]})
    return {"registry": record}


def screen_people(state: CaseState) -> dict:
    dossier = state["dossier"]
    names = set(dossier.get("dirigeants", []))
    names |= {b["nom"] for b in dossier.get("beneficiaires_effectifs", [])}
    hits = {n: screen_sanctions.invoke({"name": n})["match"] for n in sorted(names)}
    return {"sanctions": hits}


def evaluate_rules(state: CaseState) -> dict:
    verdict = evaluate(state["dossier"], state["registry"], state["sanctions"])
    return {"verdict": verdict.model_dump(mode="json")}


def narrate(state: CaseState) -> dict:
    if os.getenv("KYC_FAIL_AT") == "narrate":
        raise RuntimeError("Crash simulé avant la rédaction")
    verdict = Verdict.model_validate(state["verdict"])
    facts = json.dumps(
        {
            "societe": state["dossier"].get("denomination"),
            "constats": [f.model_dump(mode="json") for f in verdict.findings],
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


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(CaseState)
    g.add_node("fetch_registry", fetch_registry)
    g.add_node("screen_people", screen_people)
    g.add_node("evaluate_rules", evaluate_rules)
    g.add_node("narrate", narrate)
    g.add_node("human_review", human_review)
    g.add_edge(START, "fetch_registry")
    g.add_edge("fetch_registry", "screen_people")
    g.add_edge("screen_people", "evaluate_rules")
    g.add_edge("evaluate_rules", "narrate")
    g.add_edge("narrate", "human_review")
    g.add_edge("human_review", END)
    return g.compile(checkpointer=checkpointer)


def open_graph() -> tuple[Any, sqlite3.Connection]:
    Path(".checkpoints").mkdir(exist_ok=True)
    conn = sqlite3.connect(".checkpoints/cases.sqlite", check_same_thread=False)
    return build_graph(SqliteSaver(conn)), conn


def pending_interrupt(graph: Any, config: RunnableConfig) -> Any | None:
    """Retourne ce que le relecteur doit voir si le dossier est en pause, sinon None."""
    for task in graph.get_state(config).tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def advance(graph: Any, config: RunnableConfig, payload: Any) -> None:
    for update in graph.stream(payload, config, stream_mode="updates"):
        for node in update:
            if node == "__interrupt__":
                print("[human_review] en attente de validation", file=sys.stderr)
            else:
                print(f"[{node}] terminé", file=sys.stderr)


def run(dossier_path: str, thread_id: str) -> tuple[dict, bool]:
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
