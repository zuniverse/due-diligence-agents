# due-diligence-agents

Durable due diligence agents for KYC/KYB case review, built with LangChain, LangGraph and local LLMs (Ollama).

KYC (Know Your Customer) and KYB (Know Your Business) are the checks a regulated company runs before onboarding a customer: who is behind the company, does the declared information match official records, is anyone on a sanctions list. This project instructs such a case automatically, produces an auditable verdict, and is designed to escalate to a human when the case is ambiguous or risky.

> All data in this repository is synthetic. This is an engineering project, not a compliance product, and it gives no regulatory guarantee. See [Limits](#limits).

## Design principles

1. **Deterministic checks live in code, not in the model.** Registry lookup, sanctions screening, discrepancy detection, scoring and the escalation decision are plain Python. The same case always yields the same findings and the same score.
2. **The LLM only writes the explanation.** The model receives verified facts and produces a short rationale for a human reviewer. It cannot alter an address, a name or a score. The rationale is validated in code (length, complete sentences, no leaked reasoning). If the model output is unusable, a deterministic fallback text built from the verdict replaces it, so nothing unreliable ever reaches the reviewer.
3. **Every step is checkpointed.** The case is a state graph persisted after each node, so a crash resumes at the interrupted node instead of restarting from zero.
4. **Local by default.** Models run through Ollama, so case data never leaves the machine. This matters for compliance workloads.

## Architecture

The project is organised in three layers, plus a pure domain layer:

| Layer             | Tool        | Role in this project                                                                                                           |
| :---------------- | :---------- | :----------------------------------------------------------------------------------------------------------------------------- |
| Framework         | LangChain   | Model abstraction, tools, structured output                                                                                    |
| Runtime           | LangGraph   | State graph, persistent checkpoints, resume after failure                                                                      |
| Workflow          | Temporal    | Durable orchestration around the case: waiting for missing documents, reminders, human review by signal, timeouts, audit trail |
| Harness (planned) | Deep Agents | Open-ended adverse media research delegated to a sub-agent                                                                     |

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

Around this graph, a Temporal workflow (`src/kyc/workflows/case_review.py`) waits for the required documents (with reminders), runs the graph, notifies the reviewer, waits for the review decision (a signal), handles timeouts, and writes the audit record. The workflow state lives in the Temporal server, so a worker crash or a server restart does not lose a case in progress.

## Project layout

```
data/                       synthetic registry, sanctions list and sample cases
docker-compose.yml          local Temporal dev server (state kept in a volume)
src/kyc/
  domain/
    schemas.py              Pydantic models (findings, assessment)
    scoring.py              deterministic rules and risk scoring
    completeness.py         required documents check
    prompts.py              prompt for the rationale
    narrative.py            rationale validation and deterministic fallback
  tools/
    registry.py             company registry lookup
    sanctions.py            sanctions screening
  agent/
    baseline.py             tool-calling agent without orchestration (reference)
    graph.py                LangGraph case review with persistent checkpoints
    review.py               human review from the command line (standalone mode)
    adverse_media.py        adverse media research agent (planned)
  workflows/                Temporal workflow, activities and worker
    case_review.py          Temporal workflow (documents, review, timeouts)
    activities.py           activities (analysis, notifications, audit record)
    worker.py               worker process
  cli.py                    `kyc` command to start, review and query cases
tests/                      unit tests for the scoring rules
```

## Getting started

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com).

```bash
git clone https://github.com/zuniverse/due-diligence-agents.git
cd due-diligence-agents
uv sync
ollama pull gemma4:e2b
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

### Run the durable workflow

Start the local Temporal server (its state is stored in a Docker volume):

```bash
docker compose up -d
```

In one terminal, start the worker:

```bash
uv run python -m kyc.workflows.worker
```

In another terminal, drive a case:

```bash
uv run python -m kyc.cli start data/dossiers/dossier_001.json case-1 --pieces kbis statuts piece_identite
uv run python -m kyc.cli status case-1
uv run python -m kyc.cli decide case-1 approved --reviewer alice --comment "ok"
uv run python -m kyc.cli result case-1
```

Omit `--pieces` (or pass fewer) to see the workflow wait for the missing documents; send them with `kyc.cli document`. A case that requires review waits for a `decide` command; without a decision within the review delay (5 days by default, `--review-wait` in seconds), it ends as `review_timeout`. The Temporal web UI is at http://localhost:8233. The audit record is written to `.audit/<case-id>.json`.

`docker compose down` keeps the volume, so cases survive a server restart. `docker compose down -v` deletes it.

Run the tests:

```bash
uv run pytest
```

### Choosing a model

The model is configurable through an environment variable and defaults to `gemma4:e2b`:

```bash
KYC_MODEL=qwen3:4b-instruct uv run python -m kyc.agent.graph data/dossiers/dossier_001.json run-2
```

#### Model choice

Only the rationale is written by the model, but the choice still mattered:

- `qwen3:4b` (a thinking variant) ignored the instruction to disable reasoning and put its chain of thought in the answer, truncated by the token limit. This is what motivated the validation and the fallback text.
- `qwen3:4b-instruct` answers directly, but the text was flat and occasionally incoherent.
- `gemma4:e2b` was kept for the quality of its French and its small footprint (`ollama ps` showed under 1 GB resident on a CPU-only machine).

These observations come from a handful of runs, not a benchmark. An evaluation set to compare models on fallback rate and faithfulness is planned.

### Testing crash recovery

`KYC_FAIL_AT=narrate` makes the last node fail on purpose. Run the same case again with the same thread id, without the variable, and only the failed node is replayed:

```bash
KYC_FAIL_AT=narrate uv run python -m kyc.agent.graph data/dossiers/dossier_001.json crash-1
uv run python -m kyc.agent.graph data/dossiers/dossier_001.json crash-1
```

Expected output on the second run: `Reprise avant le noeud : ('narrate',)`, then only `[narrate] terminé`.

Checkpoints are stored in `.checkpoints/cases.sqlite` (ignored by git).

## What the baseline taught

`baseline.py` is a hand-written tool-calling loop with no orchestration. It is kept unchanged as a reference. Compared with the graph on the same case:

| Point                            | Baseline                               | Graph                                 |
| :------------------------------- | :------------------------------------- | :------------------------------------ |
| Score                            | chosen by the model, no rule behind it | computed from explicit rules          |
| Score and risk level consistency | not guaranteed                         | guaranteed by code                    |
| Data fidelity                    | the model re-types addresses           | values come straight from the sources |
| Tool calls                       | duplicates observed                    | one screening per distinct person     |
| Crash recovery                   | none, everything restarts              | resumes at the interrupted node       |
| What varies between runs         | everything                             | only the wording of the rationale     |

## Status

- Done: baseline agent, LangGraph case review with persistent checkpoints, deterministic scoring with unit tests, human review step (pause and resume with LangGraph `interrupt`), durable workflow with Temporal (missing documents, reminders, review by signal, timeouts, audit record), validated rationale with a deterministic fallback.
- Planned: adverse media research with Deep Agents, a quality review agent, an evaluation set with expected verdicts, additional national company registers, and document ingestion from a per-client folder.

## Limits

- All data is synthetic: the registry and the sanctions list are small JSON files, and sanctions matching is an exact, case-insensitive name comparison. Real screening needs fuzzy matching, aliases and licensed data sources.
- This project does not implement any regulatory requirement and must not be used to take real onboarding decisions.
- Small local models are less reliable than large hosted ones, which is one reason the model is kept away from every decision in the pipeline.
- The Temporal server is the single-node development server (SQLite in a volume). A production deployment would use a Temporal cluster backed by PostgreSQL.
- The rationale is a reading aid. Its quality varies with the model, and a fallback text replaces it when the validation fails.

## License

MIT, see [LICENSE](LICENSE).
