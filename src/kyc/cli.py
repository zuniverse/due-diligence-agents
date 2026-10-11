import argparse
import asyncio
import json
import os
from pathlib import Path

from temporalio.client import Client

from kyc.domain.schemas import ReviewDecision
from kyc.workflows.case_review import TASK_QUEUE, CaseInput, CaseReviewWorkflow


async def amain() -> int:
    parser = argparse.ArgumentParser(prog="kyc.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="démarre l'instruction d'un dossier")
    start.add_argument("dossier")
    # L’identifiant du dossier (case_id) devient l’identifiant du workflow :
    # il doit être unique par dossier, comme un numéro de dossier métier.
    start.add_argument("case_id")
    start.add_argument("--pieces", nargs="*", default=[])
    start.add_argument("--doc-wait", type=int, default=2 * 24 * 3600, help="secondes")
    start.add_argument(
        "--review-wait", type=int, default=5 * 24 * 3600, help="secondes"
    )

    for name in ("status", "result"):
        sub.add_parser(name).add_argument("case_id")

    doc = sub.add_parser("document", help="signale la réception d'une pièce")
    doc.add_argument("case_id")
    doc.add_argument("name")

    decide = sub.add_parser("decide", help="valide ou rejette un dossier")
    decide.add_argument("case_id")
    decide.add_argument("decision", choices=["approved", "rejected"])
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--comment", default="")

    args = parser.parse_args()
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))

    if args.cmd == "start":
        case = CaseInput(
            dossier=json.loads(Path(args.dossier).read_text()),
            pieces=args.pieces,
            doc_wait_seconds=args.doc_wait,
            review_wait_seconds=args.review_wait,
        )
        await client.start_workflow(
            CaseReviewWorkflow.run, case, id=args.case_id, task_queue=TASK_QUEUE
        )
        print(f"Dossier {args.case_id} démarré")
        return 0

    handle = client.get_workflow_handle(args.case_id)
    if args.cmd == "status":
        print(await handle.query(CaseReviewWorkflow.status))
    elif args.cmd == "document":
        await handle.signal(CaseReviewWorkflow.document_received, args.name)
    elif args.cmd == "decide":
        decision = ReviewDecision(
            decision=args.decision, reviewer=args.reviewer, comment=args.comment
        )
        await handle.signal(
            CaseReviewWorkflow.review_decision, decision.model_dump(mode="json")
        )
    elif args.cmd == "result":
        print(json.dumps(await handle.result(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
