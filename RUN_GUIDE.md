# Lab 23 Run Guide

This guide shows the simplest way to install, verify, run the complete lab, demo real HITL approval, launch the Streamlit UI, and inspect all extension evidence.

## 1. Install

Use Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,sqlite,ui]'
```

The `sqlite` extra is needed for durable local checkpoints. The `ui` extra installs Streamlit for the approval interface.

## 2. One-command full lab run

Run everything needed for the submitted evidence with one command:

```bash
make run-all
```

This command runs:

- baseline grading scenarios
- extended mock scenarios
- SQLite persistence scenarios
- crash-resume demo using the same `thread_id`
- time-travel replay from a previous checkpoint
- parallel fan-out verification for two mock tool branches
- Mermaid graph export

It writes **one consolidated report only**:

```text
reports/lab_report.md
```

It also writes supporting evidence files:

```text
outputs/metrics.json
outputs/metrics_extended.json
outputs/metrics_sqlite.json
outputs/graph.mmd
outputs/checkpoints.sqlite
outputs/crash_resume.sqlite
outputs/time_travel.sqlite
```

SQLite may also create `-wal` and `-shm` sidecar files. The report keeps the baseline grading metrics in the Scenario results section and folds all extension evidence into the existing Extension work section.

## 3. Verify locally

```bash
make test
make lint
make typecheck
make grade-local
```

Expected results:

- pytest passes
- ruff passes
- mypy passes
- `outputs/metrics.json` validates successfully

## 4. Optional individual commands

These commands are available for debugging, but `make run-all` is the normal one-command workflow.

```bash
make run-scenarios     # baseline metrics + reports/lab_report.md
make run-extended      # extended metrics only; no separate report
make run-sqlite        # SQLite metrics only; no separate report
make verify-extensions # crash-resume, time-travel, and parallel fan-out evidence
make export-diagram    # outputs/graph.mmd
```

## 5. Inspect checkpoint history

After `make run-all`, use Python to confirm state history exists:

```bash
python - <<'PY'
from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer

graph = build_graph(build_checkpointer('sqlite', 'outputs/checkpoints.sqlite'))
history = list(graph.get_state_history({'configurable': {'thread_id': 'thread-S01_simple'}}))
print('checkpoints:', len(history))
print('latest keys:', sorted(history[0].values.keys()) if history else [])
PY
```

For the explicit time-travel extension, `make run-all` also replays from an earlier checkpoint in `outputs/time_travel.sqlite` and records the result in `reports/lab_report.md`.

## 6. Real HITL approval with interrupt/resume

The default test and scenario runs use mock approval so they can run unattended. To use real HITL, set this environment variable:

```bash
export LANGGRAPH_INTERRUPT=true
```

When a risky ticket reaches `approval_node`, it calls LangGraph `interrupt()` with the proposed action, query, risk level, and resume instructions. Resume with a decision shaped like this:

```python
{"approved": True, "reviewer": "human-reviewer", "comment": "Approved after verification"}
```

Set `LANGGRAPH_INTERRUPT=false` or unset it before running automated tests.

## 7. Streamlit HITL UI

```bash
LANGGRAPH_INTERRUPT=true streamlit run streamlit_app.py
```

In the browser:

1. Choose a prepared mock support ticket from the dropdown.
2. Click **Run selected ticket**.
3. If the ticket is risky, review the interrupt payload.
4. Click **Approve action** to resume into the tool/evaluate/answer path, or **Reject action** to resume into clarification.
5. Non-risky tickets complete immediately and show route, retry count, audit trail, and mock tool evidence.

The UI keeps the internal LangGraph `thread_id` hidden because it is only needed for checkpoint/resume behavior.

## 8. Parallel fan-out behavior

Tool and approved-risky routes now dispatch two mock branches with `Send()`:

- `account_tool`
- `policy_tool`

Both branches append evidence to the same state using the existing `tool_results` and `events` reducers. `evaluate_node` checks the current attempt's merged evidence before deciding whether to retry or answer.

## 9. Clean generated files

```bash
make clean
```
