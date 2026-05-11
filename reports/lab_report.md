# Day 08 Lab Report

## 1. student

- Name: Bùi Trần Gia Bảo - 2A202600009
- Repo/commit: Local lab23 submission
- Date: 11/05/2026

## 2. Architecture

The workflow is a support-ticket LangGraph state machine with explicit node boundaries:

`START -> intake -> classify`, then conditional routing sends the ticket through one
of five paths:

- `simple -> answer -> finalize -> END`
- `tool -> tool -> account_tool/policy_tool -> evaluate -> answer -> finalize -> END`
- `missing_info -> clarify -> finalize -> END`
- `risky -> risky_action -> approval -> tool -> account_tool/policy_tool -> evaluate -> answer -> finalize -> END`
- `error -> retry -> tool -> account_tool/policy_tool -> evaluate -> retry/dead_letter -> finalize -> END`

The classifier uses keyword and state logic rather than scenario IDs. Routing priority
follows the lab instructions: risky keywords first, then tool keywords, short/vague
missing-info queries, error keywords, and finally the simple default.

## 3. State schema

| Field               | Reducer   | Why                                                  |
| ------------------- | --------- | ---------------------------------------------------- |
| `thread_id`         | overwrite | Stable LangGraph checkpoint key for one run.         |
| `scenario_id`       | overwrite | Identifies the scenario for metrics only.            |
| `query`             | overwrite | Intake stores the normalized user request.           |
| `route`             | overwrite | The current route classification.                    |
| `risk_level`        | overwrite | Latest risk assessment for approval decisions.       |
| `attempt`           | overwrite | Current retry attempt count.                         |
| `max_attempts`      | overwrite | Scenario/configured retry bound.                     |
| `final_answer`      | overwrite | Final user-facing response.                          |
| `pending_question`  | overwrite | Clarification requested when data is missing.        |
| `proposed_action`   | overwrite | Risky action package sent to HITL approval.          |
| `approval`          | overwrite | Latest human/mock approval decision.                 |
| `evaluation_result` | overwrite | Retry loop gate.                                     |
| `should_retry`      | overwrite | Scenario switch for transient mock tool failures.    |
| `messages`          | append    | Lightweight conversation/audit messages.             |
| `tool_results`      | append    | Preserves all mock tool attempts for retry analysis. |
| `errors`            | append    | Preserves retry and dead-letter evidence.            |
| `events`            | append    | Node-level audit trail used by metrics.              |

## 4. Scenario results

Baseline grading scenarios from `data/sample/scenarios.jsonl`:

- Total scenarios: 7
- Success rate: 100.00%
- Average nodes visited: 7.86
- Total retries: 3
- Total interrupts: 2
- Resume/state-history evidence: True

| Scenario        | Expected route | Actual route | Success | Retries | Interrupts | Errors                                                                                         |
| --------------- | -------------- | ------------ | ------: | ------: | ---------: | ---------------------------------------------------------------------------------------------- |
| S01_simple      | simple         | simple       |    True |       0 |          0 | -                                                                                              |
| S02_tool        | tool           | tool         |    True |       0 |          0 | -                                                                                              |
| S03_missing     | missing_info   | missing_info |    True |       0 |          0 | -                                                                                              |
| S04_risky       | risky          | risky        |    True |       0 |          1 | -                                                                                              |
| S05_error       | error          | error        |    True |       2 |          0 | retry attempt=1 max_attempts=3 exhausted=False; retry attempt=2 max_attempts=3 exhausted=False |
| S06_delete      | risky          | risky        |    True |       0 |          1 | -                                                                                              |
| S07_dead_letter | error          | error        |    True |       1 |          0 | retry attempt=1 max_attempts=1 exhausted=True; dead_letter scenario=S07_dead_letter attempt=1  |

## 5. Failure analysis

1. Retry or tool failure: error-route tickets intentionally enter `retry` before
   the tool. The tool returns a transient `ERROR:` result for early attempts,
   `evaluate` marks it as `needs_retry`, and `route_after_retry` either loops
   back to `tool` or sends the request to `dead_letter` at the retry bound.
2. Risky action without approval: risky requests must pass through
   `risky_action` and `approval`. Approved decisions continue to the tool.
   Rejections route to `clarify`, preventing unapproved external actions.

## 6. Persistence / recovery evidence

`build_checkpointer` supports `memory` for tests and `sqlite` for durable local
checkpoints. Every scenario uses `thread_id = thread-<scenario_id>`, passed
through LangGraph `configurable.thread_id`. With SQLite enabled, checkpoints
are stored in `outputs/checkpoints.sqlite`, and state history can be inspected
with `graph.get_state_history(...)` after a run.

## 7. Extension work

The single-command workflow writes extension evidence here instead of creating
separate extension report files.

| Extension evidence      | Scenarios | Success rate | Retries | Interrupts | Resume evidence |
| ----------------------- | --------: | -----------: | ------: | ---------: | --------------: |
| Extended mock scenarios |        12 |      100.00% |       5 |          3 |            True |
| SQLite persistence run  |         7 |      100.00% |       3 |          2 |            True |

Detailed extension evidence:

| Extension          | Verified | Evidence                                                                                                              |
| ------------------ | -------: | --------------------------------------------------------------------------------------------------------------------- |
| Parallel fan-out   |      yes | Observed branches: `account_tool, policy_tool`; merged tool results: 2.                                               |
| Crash-resume       |      yes | Thread `extension-crash-resume` interrupted before restart: yes; history checkpoints 5 -> 11; approval observed: yes. |
| Time travel replay |      yes | Thread `extension-time-travel` had 9 checkpoints; replayed from step 5; final answer matched: yes.                    |

Completed requested extensions:

- Real HITL: set `LANGGRAPH_INTERRUPT=true`; `approval_node` calls `interrupt()`.
- Streamlit UI: `streamlit_app.py` provides approve/reject controls and resumes
  interrupted risky requests with the supplied decision.
- Graph diagram: Mermaid graph text is exported to `outputs\graph.mmd`.
- Crash-resume: a risky request is interrupted, the graph/checkpointer is rebuilt,
  and the same thread is resumed from SQLite with approval.
- Time travel replay: `get_state_history()` locates an earlier checkpoint and
  replays the graph from that checkpoint.
- Parallel fan-out: `Send()` dispatches two mock tool branches, and append reducers
  merge their evidence before evaluation.
- SQLite persistence: `configs/lab_sqlite.yaml` runs with `checkpointer: sqlite`,
  `SqliteSaver`, and WAL mode enabled.
- Optional extended mock scenarios: `configs/lab_extended.yaml` runs additional
  mock tickets for broader local coverage without replacing the baseline set.

## 8. Improvement plan

With one more day, the first production improvements would be structured real
tool integrations, durable dead-letter storage with alerting, richer approval
roles, latency timing per node, and stricter validation of tool evidence.
