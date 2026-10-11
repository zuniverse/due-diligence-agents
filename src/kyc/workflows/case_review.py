from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from kyc.domain.completeness import missing_documents
    from kyc.workflows import activities

TASK_QUEUE = "case-review"

ANALYSIS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=8,
)
SHORT = timedelta(seconds=30)


@dataclass
class CaseInput:
    dossier: dict
    pieces: list[str] = field(default_factory=list)
    doc_wait_seconds: int = 2 * 24 * 3600
    review_wait_seconds: int = 5 * 24 * 3600
    max_reminders: int = 3


@workflow.defn
class CaseReviewWorkflow:
    def __init__(self) -> None:
        self._received: list[str] = []
        self._decision: dict | None = None
        self._status = "started"

    @workflow.signal
    def document_received(self, name: str) -> None:
        self._received.append(name)

    @workflow.signal
    def review_decision(self, decision: dict) -> None:
        self._decision = decision

    @workflow.query
    def status(self) -> str:
        return self._status

    @workflow.run
    async def run(self, case: CaseInput) -> dict:
        """Règle d’or : le code d’un workflow doit être déterministe, car Temporal
        le rejoue depuis l’historique pour reconstruire l’état. Pas de
        datetime.now(), de hasard, de réseau ni de fichier ici : on utilise
        workflow.now(), et tout le reste va dans des activités. C’est pour cela que
        le modèle et les écritures y sont déportés."""
        wf_id = workflow.info().workflow_id
        pieces = list(case.pieces)

        # 1. Pièces manquantes : attente durable avec relances
        timeouts = 0
        while True:
            missing = missing_documents(pieces)
            if not missing:
                break
            if timeouts > case.max_reminders:
                self._status = "abandoned"
                return {"status": "abandoned", "missing": missing}
            self._status = "waiting_documents: " + ", ".join(missing)
            await workflow.execute_activity(
                activities.notify_missing_documents,
                args=[wf_id, missing],
                start_to_close_timeout=SHORT,
            )
            try:
                await workflow.wait_condition(
                    lambda: bool(self._received),
                    timeout=timedelta(seconds=case.doc_wait_seconds),
                )
            except TimeoutError:
                timeouts += 1
                continue
            pieces += self._received
            self._received.clear()

        # 2. Analyse (le graphe LangGraph)
        self._status = "analyzing"
        assessment = await workflow.execute_activity(
            activities.analyze_case,
            args=[case.dossier, wf_id],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=ANALYSIS_RETRY,
        )

        # 3. Décision
        if assessment["requires_human_review"]:
            self._status = "waiting_review"
            await workflow.execute_activity(
                activities.notify_reviewer,
                args=[wf_id, assessment],
                start_to_close_timeout=SHORT,
            )
            try:
                await workflow.wait_condition(
                    lambda: self._decision is not None,
                    timeout=timedelta(seconds=case.review_wait_seconds),
                )
            except TimeoutError:
                self._status = "review_timeout"
                return {"status": "review_timeout", "assessment": assessment}
            assert self._decision is not None
            decision = self._decision
        else:
            decision = {
                "decision": "approved",
                "reviewer": "automatic",
                "comment": "Risque faible : validation automatique",
                "decided_at": workflow.now().isoformat(),
            }

        # 4. Enregistrement
        await workflow.execute_activity(
            activities.record_decision,
            args=[wf_id, assessment, decision],
            start_to_close_timeout=SHORT,
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        self._status = "completed"
        return {"status": "completed", "assessment": assessment, "decision": decision}
