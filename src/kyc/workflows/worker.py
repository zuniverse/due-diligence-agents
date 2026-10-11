import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from kyc.workflows import activities
from kyc.workflows.case_review import TASK_QUEUE, CaseReviewWorkflow


async def main() -> None:
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[CaseReviewWorkflow],
            activities=[
                activities.analyze_case,
                activities.notify_missing_documents,
                activities.notify_reviewer,
                activities.record_decision,
            ],
            activity_executor=pool,
        )
        print("Worker démarré, en attente de dossiers")
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
