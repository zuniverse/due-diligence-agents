import argparse
import json
import sys

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from kyc.agent.graph import advance, open_graph, pending_interrupt


def _config(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def main() -> int:
    parser = argparse.ArgumentParser(prog="kyc.agent.review")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending", help="liste les dossiers en attente")
    show = sub.add_parser("show", help="affiche un dossier en attente")
    show.add_argument("thread")
    decide = sub.add_parser("decide", help="valide ou rejette un dossier")
    decide.add_argument("thread")
    decide.add_argument("decision", choices=["approved", "rejected"])
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--comment", default="")
    args = parser.parse_args()

    graph, conn = open_graph()

    if args.cmd == "pending":
        rows = conn.execute("select distinct thread_id from checkpoints").fetchall()
        found = False
        for (thread,) in rows:
            payload = pending_interrupt(graph, _config(thread))
            if payload is not None:
                found = True
                a = payload["assessment"]
                print(f"{thread}\tscore={a['score']}\trisque={a['risk_level']}")
        if not found:
            print("Aucun dossier en attente")
        return 0

    payload = pending_interrupt(graph, _config(args.thread))
    if payload is None:
        print("Aucun dossier en attente pour ce thread", file=sys.stderr)
        return 1

    if args.cmd == "show":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    answer = {
        "decision": args.decision,
        "reviewer": args.reviewer,
        "comment": args.comment,
    }
    advance(graph, _config(args.thread), Command(resume=answer))
    decision = graph.get_state(_config(args.thread)).values["decision"]
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
