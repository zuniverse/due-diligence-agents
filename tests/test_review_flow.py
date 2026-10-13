import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from kyc.agent import graph as g

DOSSIERS = Path(__file__).resolve().parents[1] / "data" / "dossiers"


def fake_narrate(state):
    return {"assessment": {**state["verdict"], "rationale": "test"}}


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setattr(g, "narrate", fake_narrate)
    return g.build_graph(MemorySaver())


def load(name: str) -> dict:
    return json.loads((DOSSIERS / name).read_text())


def test_risky_case_pauses_then_resumes(graph):
    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"dossier": load("dossier_001.json")}, config)
    assert g.pending_interrupt(graph, config) is not None

    graph.invoke(
        Command(resume={"decision": "rejected", "reviewer": "test", "comment": "x"}),
        config,
    )
    assert graph.get_state(config).values["decision"]["decision"] == "rejected"


def test_clean_case_is_auto_approved(graph):
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"dossier": load("dossier_002.json")}, config)
    assert g.pending_interrupt(graph, config) is None
    assert graph.get_state(config).values["decision"]["reviewer"] == "automatic"


def test_delmasse_case_is_clean_without_media(graph):
    config = {"configurable": {"thread_id": "t3"}}
    graph.invoke({"dossier": load("dossier_003.json")}, config)
    values = graph.get_state(config).values
    assert values["verdict"]["score"] == 0
    assert values["decision"]["reviewer"] == "automatic"
