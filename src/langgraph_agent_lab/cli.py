"""CLI for the lab."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .report import write_report
from .scenarios import load_scenarios
from .state import Route, Scenario, initial_state

app = typer.Typer(no_args_is_help=True)


def _state_history_available(graph: Any, thread_id: str) -> bool:
    """Return True when LangGraph can read at least one checkpoint for a thread."""
    try:
        history = list(graph.get_state_history({"configurable": {"thread_id": thread_id}}))
    except Exception:
        return False
    return len(history) > 0


def _reset_thread(checkpointer: Any | None, thread_id: str) -> None:
    """Remove stale checkpoints so repeated scenario runs keep deterministic metrics."""
    if checkpointer is None or not hasattr(checkpointer, "delete_thread"):
        return
    try:
        checkpointer.delete_thread(thread_id)
    except Exception:
        return


def _load_config(config: Path) -> dict[str, Any]:
    """Load a YAML lab configuration file."""
    return yaml.safe_load(config.read_text(encoding="utf-8"))


def _run_config(config: Path, output: Path) -> MetricsReport:
    """Run all scenarios for one config and write only the metrics JSON."""
    cfg = _load_config(config)
    scenarios = load_scenarios(cfg["scenarios_path"])
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), cfg.get("database_url"))
    graph = build_graph(checkpointer=checkpointer)
    metrics = []
    first_thread_id: str | None = None
    for scenario in scenarios:
        state = initial_state(scenario)
        first_thread_id = first_thread_id or state["thread_id"]
        _reset_thread(checkpointer, state["thread_id"])
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        final_state = graph.invoke(state, config=run_config)
        metrics.append(
            metric_from_state(
                final_state,
                scenario.expected_route.value,
                scenario.requires_approval,
            )
        )
    resume_success = _state_history_available(graph, first_thread_id) if first_thread_id else False
    report = summarize_metrics(metrics, resume_success=resume_success)
    write_metrics(report, output)
    return report


def _export_mermaid(output: Path) -> None:
    """Export the graph Mermaid diagram."""
    graph = build_graph(checkpointer=None)
    mermaid = graph.get_graph().draw_mermaid()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(mermaid, encoding="utf-8")


def _remove_obsolete_extension_reports(report_output: Path) -> None:
    """Remove older generated extension reports so one command leaves one report file."""
    for name in ("lab_report_extended.md", "lab_report_sqlite.md"):
        obsolete = report_output.parent / name
        if obsolete != report_output and obsolete.exists():
            obsolete.unlink()


def _remove_sqlite_files(db_path: Path) -> None:
    """Delete SQLite database files from prior local extension demos."""
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


def _with_interrupt_enabled() -> str | None:
    """Enable LangGraph interrupt mode and return the previous env value."""
    previous = os.environ.get("LANGGRAPH_INTERRUPT")
    os.environ["LANGGRAPH_INTERRUPT"] = "true"
    return previous


def _restore_interrupt(previous: str | None) -> None:
    """Restore LANGGRAPH_INTERRUPT after a demo run."""
    if previous is None:
        os.environ.pop("LANGGRAPH_INTERRUPT", None)
    else:
        os.environ["LANGGRAPH_INTERRUPT"] = previous


def _has_interrupt(payload: dict[str, Any]) -> bool:
    """Return True when a graph payload contains a LangGraph interrupt."""
    return bool(payload.get("__interrupt__"))


def _demo_parallel_fanout() -> dict[str, Any]:
    """Verify the Send()-based two-tool fan-out extension."""
    scenario = Scenario(
        id="EXT_parallel_fanout",
        query="Please lookup order status for order 12345",
        expected_route=Route.TOOL,
    )
    state = initial_state(scenario)
    state["thread_id"] = "extension-parallel-fanout"
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    nodes = [event.get("node") for event in result.get("events", [])]
    tool_results = result.get("tool_results", []) or []
    observed_branches = sorted({node for node in nodes if node in {"account_tool", "policy_tool"}})
    return {
        "success": observed_branches == ["account_tool", "policy_tool"] and len(tool_results) >= 2,
        "thread_id": state["thread_id"],
        "branches": observed_branches,
        "tool_result_count": len(tool_results),
    }


def _demo_crash_resume(db_path: Path) -> dict[str, Any]:
    """Simulate process restart by interrupting, rebuilding the graph, and resuming."""
    from langgraph.types import Command

    _remove_sqlite_files(db_path)
    previous_interrupt = _with_interrupt_enabled()
    thread_id = "extension-crash-resume"
    config = {"configurable": {"thread_id": thread_id}}
    try:
        scenario = Scenario(
            id="EXT_crash_resume",
            query="Refund this customer and send confirmation email",
            expected_route=Route.RISKY,
            requires_approval=True,
        )
        state = initial_state(scenario)
        state["thread_id"] = thread_id

        checkpointer_before_restart = build_checkpointer("sqlite", str(db_path))
        graph_before_restart = build_graph(checkpointer=checkpointer_before_restart)
        interrupted_payload = graph_before_restart.invoke(state, config=config)
        interrupted = _has_interrupt(interrupted_payload)
        history_before_restart = list(graph_before_restart.get_state_history(config))
        conn = getattr(checkpointer_before_restart, "conn", None)
        if conn is not None:
            conn.close()

        graph_after_restart = build_graph(checkpointer=build_checkpointer("sqlite", str(db_path)))
        final_state = graph_after_restart.invoke(
            Command(
                resume={
                    "approved": True,
                    "reviewer": "extension-demo",
                    "comment": "Approved after simulated process restart",
                }
            ),
            config=config,
        )
        history_after_restart = list(graph_after_restart.get_state_history(config))
        approval = final_state.get("approval") or {}
        return {
            "success": bool(
                interrupted
                and approval.get("approved") is True
                and final_state.get("final_answer")
                and len(history_after_restart) > len(history_before_restart)
            ),
            "thread_id": thread_id,
            "interrupted_before_restart": interrupted,
            "history_before_restart": len(history_before_restart),
            "history_after_restart": len(history_after_restart),
            "final_route": final_state.get("route"),
            "approval_observed": approval.get("approved") is True,
        }
    finally:
        _restore_interrupt(previous_interrupt)


def _demo_time_travel(db_path: Path) -> dict[str, Any]:
    """Replay from a previous checkpoint using graph.get_state_history()."""
    _remove_sqlite_files(db_path)
    thread_id = "extension-time-travel"
    config = {"configurable": {"thread_id": thread_id}}
    scenario = Scenario(
        id="EXT_time_travel",
        query="Please lookup order status for order 12345",
        expected_route=Route.TOOL,
    )
    state = initial_state(scenario)
    state["thread_id"] = thread_id
    graph = build_graph(checkpointer=build_checkpointer("sqlite", str(db_path)))
    final_state = graph.invoke(state, config=config)
    history = list(graph.get_state_history(config))
    replay_checkpoint = next((snapshot for snapshot in history if "answer" in snapshot.next), None)
    replay_state = graph.invoke(None, config=replay_checkpoint.config) if replay_checkpoint else {}
    replay_matches = replay_state.get("final_answer") == final_state.get("final_answer")
    return {
        "success": bool(replay_checkpoint and replay_matches and len(history) > 1),
        "thread_id": thread_id,
        "history_count": len(history),
        "replayed_from_step": (
            replay_checkpoint.metadata.get("step") if replay_checkpoint else None
        ),
        "replay_final_answer_matches": replay_matches,
    }


def _run_extension_demos() -> dict[str, Any]:
    """Run all non-baseline extension demonstrations used by the report."""
    return {
        "parallel_fanout": _demo_parallel_fanout(),
        "crash_resume": _demo_crash_resume(Path("outputs/crash_resume.sqlite")),
        "time_travel": _demo_time_travel(Path("outputs/time_travel.sqlite")),
    }


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    report = _run_config(config, output)
    cfg = _load_config(config)
    if cfg.get("report_path"):
        write_report(report, cfg["report_path"])
    typer.echo(f"Wrote metrics to {output}")


@app.command("run-all")
def run_all(
    baseline_config: Annotated[
        Path,
        typer.Option("--baseline-config"),
    ] = Path("configs/lab.yaml"),
    extended_config: Annotated[
        Path,
        typer.Option("--extended-config"),
    ] = Path("configs/lab_extended.yaml"),
    sqlite_config: Annotated[
        Path,
        typer.Option("--sqlite-config"),
    ] = Path("configs/lab_sqlite.yaml"),
    baseline_output: Annotated[
        Path,
        typer.Option("--baseline-output"),
    ] = Path("outputs/metrics.json"),
    extended_output: Annotated[
        Path,
        typer.Option("--extended-output"),
    ] = Path("outputs/metrics_extended.json"),
    sqlite_output: Annotated[
        Path,
        typer.Option("--sqlite-output"),
    ] = Path("outputs/metrics_sqlite.json"),
    diagram_output: Annotated[
        Path,
        typer.Option("--diagram-output"),
    ] = Path("outputs/graph.mmd"),
    report_output: Annotated[
        Path,
        typer.Option("--report"),
    ] = Path("reports/lab_report.md"),
) -> None:
    """Run baseline plus extension evidence, then write one consolidated report."""
    _remove_obsolete_extension_reports(report_output)
    baseline_report = _run_config(baseline_config, baseline_output)
    extended_report = _run_config(extended_config, extended_output)
    sqlite_report = _run_config(sqlite_config, sqlite_output)
    extension_evidence = _run_extension_demos()
    _export_mermaid(diagram_output)
    write_report(
        baseline_report,
        report_output,
        extension_metrics={
            "extended_mock_scenarios": extended_report,
            "sqlite_persistence": sqlite_report,
        },
        extension_evidence=extension_evidence,
        diagram_path=diagram_output,
    )
    typer.echo(f"Wrote baseline metrics to {baseline_output}")
    typer.echo(f"Wrote extension metrics to {extended_output}")
    typer.echo(f"Wrote SQLite metrics to {sqlite_output}")
    typer.echo(f"Wrote Mermaid graph diagram to {diagram_output}")
    typer.echo(f"Wrote consolidated report to {report_output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


@app.command("export-diagram")
def export_diagram(
    output: Annotated[Path, typer.Option("--output")] = Path("outputs/graph.mmd"),
) -> None:
    """Export the compiled graph as Mermaid text."""
    _export_mermaid(output)
    typer.echo(f"Wrote Mermaid graph diagram to {output}")


@app.command("verify-extensions")
def verify_extensions() -> None:
    """Run extension demos without regenerating baseline metrics or the report."""
    evidence = _run_extension_demos()
    typer.echo(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app()
