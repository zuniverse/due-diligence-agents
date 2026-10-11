import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from kyc.agent import graph as g
from kyc.workflows import activities
from kyc.workflows.case_review import TASK_QUEUE, CaseReviewWorkflow

DOSSIERS = Path(__file__).resolve().parents[1] / "data" / "dossiers"


class _Conn:
    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def memory_graph(monkeypatch):
    monkeypatch.setattr(
        g, "narrate", lambda s: {"assessment": {**s["verdict"], "rationale": "test"}}
    )
    graph = g.build_graph(MemorySaver())
    monkeypatch.setattr(activities, "open_graph", lambda: (graph, _Conn()))


async def _run(name: str, review: dict | None) -> dict:
    dossier = json.loads((DOSSIERS / name).read_text())
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[CaseReviewWorkflow],
                activities=[activities.instruct_case, activities.resume_case],
                activity_executor=pool,
            ):
                handle = await env.client.start_workflow(
                    CaseReviewWorkflow.run,
                    args=[dossier, name],
                    id=name,
                    task_queue=TASK_QUEUE,
                )
                if review:
                    await handle.signal(CaseReviewWorkflow.submit_review, review)
                return await handle.result()


async def test_risky_case_waits_for_signal():
    review = {"decision": "rejected", "reviewer": "t", "comment": "x"}
    result = await _run("dossier_001.json", review)
    assert result["status"] == "completed"
    assert result["decision"]["decision"] == "rejected"


async def test_risky_case_escalates_on_timeout():
    result = await _run("dossier_001.json", None)
    assert result["status"] == "escalated"


async def test_clean_case_completes_without_review():
    result = await _run("dossier_002.json", None)
    assert result["decision"]["reviewer"] == "automatic"
