"""Tests du workflow CaseReviewWorkflow avec des activités factices.

Aucune dépendance à Ollama, SQLite ou au disque : seul l'enchaînement du
workflow (attentes, signaux, délais, issues) est vérifié. L'environnement à
temps accéléré fait passer des délais de plusieurs jours en quelques instants.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from kyc.workflows.case_review import TASK_QUEUE, CaseInput, CaseReviewWorkflow

ALL_PIECES = ["kbis", "statuts", "piece_identite"]
RISKY = {"siren": "000000001", "risky": True}
CLEAN = {"siren": "000000002", "risky": False}
REVIEW = {"decision": "rejected", "reviewer": "t", "comment": "x"}


@dataclass
class Calls:
    """Trace des appels reçus par les activités factices."""

    missing: list = field(default_factory=list)
    reviewer: list = field(default_factory=list)
    recorded: list = field(default_factory=list)


def make_activities(calls: Calls) -> list:
    """Activités factices, enregistrées sous les mêmes noms que les vraies."""

    @activity.defn(name="notify_missing_documents")
    async def notify_missing_documents(case_id: str, missing: list[str]) -> None:
        calls.missing.append(missing)

    @activity.defn(name="analyze_case")
    async def analyze_case(dossier: dict, case_id: str) -> dict:
        risky = bool(dossier.get("risky"))
        return {"requires_human_review": risky, "score": 90 if risky else 5}

    @activity.defn(name="notify_reviewer")
    async def notify_reviewer(case_id: str, assessment: dict) -> None:
        calls.reviewer.append(assessment)

    @activity.defn(name="record_decision")
    async def record_decision(case_id: str, assessment: dict, decision: dict) -> None:
        calls.recorded.append(decision)

    return [notify_missing_documents, analyze_case, notify_reviewer, record_decision]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


@asynccontextmanager
async def running(env: WorkflowEnvironment, calls: Calls) -> AsyncGenerator[Client]:
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[CaseReviewWorkflow],
        activities=make_activities(calls),
    ):
        yield env.client


async def start(client: Client, case: CaseInput) -> WorkflowHandle:
    return await client.start_workflow(
        CaseReviewWorkflow.run,
        case,
        id=f"test-{uuid.uuid4()}",
        task_queue=TASK_QUEUE,
    )


async def test_risky_case_waits_for_review_signal(env):
    calls = Calls()
    async with running(env, calls) as client:
        handle = await start(client, CaseInput(RISKY, pieces=ALL_PIECES))
        await handle.signal(CaseReviewWorkflow.review_decision, REVIEW)
        result = await handle.result()

    assert result["status"] == "completed"
    assert result["decision"]["decision"] == "rejected"
    assert len(calls.reviewer) == 1
    assert calls.recorded == [REVIEW]


async def test_risky_case_without_review_times_out(env):
    calls = Calls()
    async with running(env, calls) as client:
        case = CaseInput(RISKY, pieces=ALL_PIECES, review_wait_seconds=3600)
        result = await (await start(client, case)).result()

    assert result["status"] == "review_timeout"
    assert calls.recorded == []


async def test_clean_case_completes_without_review(env):
    calls = Calls()
    async with running(env, calls) as client:
        result = await (
            await start(client, CaseInput(CLEAN, pieces=ALL_PIECES))
        ).result()

    assert result["status"] == "completed"
    assert result["decision"]["reviewer"] == "automatic"
    assert calls.reviewer == []
    assert len(calls.recorded) == 1


async def test_missing_documents_are_awaited_then_case_proceeds(env):
    calls = Calls()
    async with running(env, calls) as client:
        handle = await start(client, CaseInput(CLEAN, pieces=["kbis"]))
        await handle.signal(CaseReviewWorkflow.document_received, "statuts")
        await handle.signal(CaseReviewWorkflow.document_received, "piece_identite")
        result = await handle.result()

    assert calls.missing[0] == ["statuts", "piece_identite"]
    assert result["status"] == "completed"


async def test_case_is_abandoned_when_documents_never_arrive(env):
    calls = Calls()
    async with running(env, calls) as client:
        case = CaseInput(CLEAN, pieces=["kbis"], doc_wait_seconds=60, max_reminders=1)
        result = await (await start(client, case)).result()

    assert result["status"] == "abandoned"
    assert result["missing"] == ["statuts", "piece_identite"]
    assert len(calls.missing) == 2  # première demande + 1 relance
    assert calls.recorded == []
