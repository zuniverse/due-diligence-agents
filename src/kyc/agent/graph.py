import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from kyc.domain.prompts import NARRATIVE_SYSTEM
from kyc.domain.schemas import KycAssessment, Narrative
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


def build_graph(checkpointer: SqliteSaver):
    g = StateGraph(CaseState)
    g.add_node("fetch_registry", fetch_registry)
    g.add_node("screen_people", screen_people)
    g.add_node("evaluate_rules", evaluate_rules)
    g.add_node("narrate", narrate)
    g.add_edge(START, "fetch_registry")
    g.add_edge("fetch_registry", "screen_people")
    g.add_edge("screen_people", "evaluate_rules")
    g.add_edge("evaluate_rules", "narrate")
    g.add_edge("narrate", END)
    return g.compile(checkpointer=checkpointer)


def run(dossier_path: str, thread_id: str) -> KycAssessment:
    Path(".checkpoints").mkdir(exist_ok=True)
    conn = sqlite3.connect(".checkpoints/cases.sqlite", check_same_thread=False)
    graph = build_graph(SqliteSaver(conn))
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    payload: dict | None
    pending = graph.get_state(config).next
    if pending:
        print(f"Reprise avant le noeud : {pending}", file=sys.stderr)
        payload = None
    else:
        payload = {"dossier": json.loads(Path(dossier_path).read_text())}

    for update in graph.stream(payload, config, stream_mode="updates"):
        for node in update:
            print(f"[{node}] terminé", file=sys.stderr)

    return KycAssessment.model_validate(graph.get_state(config).values["assessment"])


if __name__ == "__main__":
    path = sys.argv[1]
    thread = sys.argv[2] if len(sys.argv) > 2 else Path(path).stem
    print(run(path, thread).model_dump_json(indent=2))
