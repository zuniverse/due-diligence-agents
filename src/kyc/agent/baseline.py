import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from kyc.domain.schemas import KycAssessment
from kyc.tools import TOOLS

SYSTEM = """Tu es un analyste KYC/KYB. Pour le dossier fourni:
1. Récupère la fiche officielle de la société avec le SIREN.
2. Compare les informations déclarées (adresse, dirigeants) à la fiche.
3. Fais un screening sanctions de chaque dirigeant et bénéficiaire effectif.
Utilise les outils, n'invente aucune donnée."""

llm = ChatOllama(model="qwen3:8b", temperature=0)
llm_tools = llm.bind_tools(list(TOOLS.values()))


def run(dossier_path: str) -> KycAssessment:
    dossier = Path(dossier_path).read_text()
    messages = [SystemMessage(SYSTEM), HumanMessage(dossier)]

    for _ in range(8):  # garde-fou contre les boucles infinies
        ai = llm_tools.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            result = TOOLS[call["name"]].invoke(call["args"])
            messages.append(
                ToolMessage(content=json.dumps(result), tool_call_id=call["id"])
            )

    return llm.with_structured_output(KycAssessment).invoke(messages)


if __name__ == "__main__":
    print(run(sys.argv[1]).model_dump_json(indent=2))
