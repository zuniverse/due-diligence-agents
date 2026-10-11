import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_ollama import ChatOllama

from kyc.domain.schemas import KycAssessment
from kyc.tools import TOOLS

SYSTEM = """Tu es un analyste KYC/KYB. Pour le dossier fourni:
1. Récupère la fiche officielle de la société avec le SIREN.
2. Compare les informations déclarées (adresse, dirigeants) à la fiche.
3. Fais un screening sanctions de chaque dirigeant et bénéficiaire effectif.
Utilise les outils, n'invente aucune donnée."""

KYC_MODEL: str = os.getenv("KYC_MODEL", "gemma4:e2b")
llm = ChatOllama(model=KYC_MODEL, temperature=0, keep_alive="15m", reasoning=False)
llm_tools = llm.bind_tools(list(TOOLS.values()))


def run(dossier_path: str) -> KycAssessment:
    dossier = Path(dossier_path).read_text()
    messages: list[BaseMessage] = [SystemMessage(SYSTEM), HumanMessage(dossier)]

    for i in range(8):  # garde-fou contre les boucles infinies
        t0 = time.perf_counter()
        ai = llm_tools.invoke(messages)
        messages.append(ai)
        print(
            f"Pass {i} - time elapsed: {time.perf_counter() - t0:.1f} s - "
            f"tool_calls={[c['name'] for c in ai.tool_calls]}",
            file=sys.stderr,
        )
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            result = TOOLS[call["name"]].invoke(call["args"])
            messages.append(
                ToolMessage(content=json.dumps(result), tool_call_id=call["id"])
            )

    messages.append(
        HumanMessage(
            "Produis maintenant l'évaluation finale du dossier, "
            "au format JSON demandé, à partir des résultats des outils."
        )
    )
    t0 = time.perf_counter()
    structured = llm.with_structured_output(KycAssessment, method="json_schema")
    result = structured.invoke(messages)
    print(
        f"Structured output time elapsed: {time.perf_counter() - t0:.1f} s",
        file=sys.stderr,
    )
    return KycAssessment.model_validate(result)


if __name__ == "__main__":
    start = time.perf_counter()
    print(f"Début : {datetime.now().astimezone():%H:%M:%S}", file=sys.stderr)
    try:
        print(run(sys.argv[1]).model_dump_json(indent=2))
    finally:
        elapsed = time.perf_counter() - start
        print(
            f"Fin : {datetime.now().astimezone():%H:%M:%S} (durée : {elapsed:.1f} s)",
            file=sys.stderr,
        )
