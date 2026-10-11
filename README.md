# due-diligence-agents

Durable due diligence agents for KYC/KYB case review, built with LangChain, LangGraph and local LLMs (Ollama).

KYC (Know Your Customer) and KYB (Know Your Business) are the checks a regulated company runs before onboarding a customer: who is behind the company, does the declared information match official records, is anyone on a sanctions list. This project instructs such a case automatically, produces an auditable verdict, and is designed to escalate to a human when the case is ambiguous or risky.

> All data in this repository is synthetic. This is an engineering project, not a compliance product, and it gives no regulatory guarantee. See [Limits](#limits).

## Design principles

1. **Deterministic checks live in code, not in the model.** Registry lookup, sanctions screening, discrepancy detection, scoring and the escalation decision are plain Python. The same case always yields the same findings and the same score.
2. **The LLM only writes the explanation.** The model receives verified facts and produces a short rationale for a human reviewer. It cannot alter an address, a name or a score.
3. **Every step is checkpointed.** The case is a state graph persisted after each node, so a crash resumes at the interrupted node instead of restarting from zero.
4. **Local by default.** Models run through Ollama, so case data never leaves the machine. This matters for compliance workloads.

## Architecture

The project is organised in three layers, plus a pure domain layer:

| Layer | Tool | Role in this project |
| :--- | :--- | :--- |
| Framework | LangChain | Model abstraction, tools, structured output |
| Runtime | LangGraph | State graph, persistent checkpoints, resume after failure |
| Workflow | Temporal | Long-running orchestration around the case (waiting for missing documents, retries) |
| Harness (planned) | Deep Agents | Open-ended adverse media research delegated to a sub-agent |

Case flow, as implemented:

```
fetch_registry -> screen_people -> evaluate_rules -> narrate
   (code)            (code)           (code)          (LLM)
```

- `fetch_registry`: reads the official company record from the SIREN (French company identifier).
- `screen_people`: screens every director and beneficial owner against the sanctions list, once per distinct person.
- `evaluate_rules`: compares declared data to the record, builds the findings, computes the score and risk level, and decides whether a human must review.
- `narrate`: asks the model for a rationale, using only the verified findings.

Scoring rules live in `src/kyc/domain/scoring.py` and are unit tested without any model.

## Project layout

```
data/                       synthetic registry, sanctions list and sample cases
src/kyc/
  domain/
    schemas.py              Pydantic models (findings, assessment)
    scoring.py              deterministic rules and risk scoring
    prompts.py              prompt for the rationale
  tools/
    registry.py             company registry lookup
    sanctions.py            sanctions screening
  agent/
    baseline.py             tool-calling agent without orchestration (reference)
    graph.py                LangGraph case review with persistent checkpoints
    review.py               human review step (planned)
    adverse_media.py        adverse media research agent (planned)
  workflows/                Temporal workflow, activities and worker
  cli.py                    `kyc` command to start, review and query cases
tests/                      unit tests for the scoring rules
```

## Getting started

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com).

```bash
git clone https://github.com/zuniverse/due-diligence-agents.git
cd due-diligence-agents
uv sync
ollama pull qwen3:4b
```

Check that Ollama is running:

```bash
curl http://localhost:11434
```

Run a case through the graph. The last argument is a thread id: it identifies the run in the checkpoint database, so use a new one for each fresh run.

```bash
uv run python -m kyc.agent.graph data/dossiers/dossier_001.json run-1
```

Run the baseline for comparison:

```bash
uv run python -m kyc.agent.baseline data/dossiers/dossier_001.json
```

Run the tests:

```bash
uv run pytest
```

### Choosing a model

The model is configurable through an environment variable and defaults to `qwen3:4b`:

```bash
KYC_MODEL=qwen3:8b uv run python -m kyc.agent.graph data/dossiers/dossier_001.json run-2
```

### Testing crash recovery

`KYC_FAIL_AT=narrate` makes the last node fail on purpose. Run the same case again with the same thread id, without the variable, and only the failed node is replayed:

```bash
KYC_FAIL_AT=narrate uv run python -m kyc.agent.graph data/dossiers/dossier_001.json crash-1
uv run python -m kyc.agent.graph data/dossiers/dossier_001.json crash-1
```

Expected output on the second run: `Reprise avant le noeud : ('narrate',)`, then only `[narrate] terminé`.

Checkpoints are stored in `.checkpoints/cases.sqlite` (ignored by git).

### Durable workflow (Temporal)

Requires the [Temporal CLI](https://docs.temporal.io/cli). In three terminals:

```bash
temporal server start-dev
uv run python -m kyc.workflows.worker
uv run kyc start data/dossiers/dossier_001.json --case-id wf-1
uv run kyc status wf-1            # awaiting_review
uv run kyc review wf-1 rejected --reviewer paul --comment "Sanction confirmée"
uv run kyc result wf-1
```

The workflow runs the graph in an activity, waits for the `submit_review` signal if a human must decide, then resumes the graph. After 3 days without review the case ends as `escalated`.

## What the baseline taught

`baseline.py` is a hand-written tool-calling loop with no orchestration. It is kept unchanged as a reference. Compared with the graph on the same case:

| Point | Baseline | Graph |
| :--- | :--- | :--- |
| Score | chosen by the model, no rule behind it | computed from explicit rules |
| Score and risk level consistency | not guaranteed | guaranteed by code |
| Data fidelity | the model re-types addresses | values come straight from the sources |
| Tool calls | duplicates observed | one screening per distinct person |
| Crash recovery | none, everything restarts | resumes at the interrupted node |
| What varies between runs | everything | only the wording of the rationale |

## Status

- Done: baseline agent, LangGraph case review with persistent checkpoints, deterministic scoring with unit tests, human review step (LangGraph `interrupt`), durable Temporal workflow (review signal, 3 day timeout with escalation).
- Planned: adverse media research with Deep Agents, a quality review agent, and an evaluation set with expected verdicts.

## Limits

- All data is synthetic: the registry and the sanctions list are small JSON files, and sanctions matching is an exact, case-insensitive name comparison. Real screening needs fuzzy matching, aliases and licensed data sources.
- This project does not implement any regulatory requirement and must not be used to take real onboarding decisions.
- Small local models are less reliable than large hosted ones, which is one reason the model is kept away from every decision in the pipeline.

## License

MIT, see [LICENSE](LICENSE).
