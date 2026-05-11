# Run with: LANGGRAPH_INTERRUPT=true streamlit run streamlit_app.py

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any

import streamlit as st
from langgraph.types import Command

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.scenarios import load_scenarios
from langgraph_agent_lab.state import Route, Scenario, initial_state

SCENARIO_PATH = Path("data/sample/scenarios_extended.jsonl")
FALLBACK_SCENARIO_PATH = Path("data/sample/scenarios.jsonl")
CHECKPOINT_DB = "outputs/hitl_checkpoints.sqlite"


@st.cache_resource
def _build_app_graph() -> Any:
    """Build one interrupt-enabled graph for the Streamlit session."""
    os.environ["LANGGRAPH_INTERRUPT"] = "true"
    checkpointer = build_checkpointer("sqlite", CHECKPOINT_DB)
    return build_graph(checkpointer=checkpointer)


@st.cache_data
def _load_demo_scenarios() -> list[Scenario]:
    """Load the existing mock scenarios so users choose from known grading cases."""
    path = SCENARIO_PATH if SCENARIO_PATH.exists() else FALLBACK_SCENARIO_PATH
    return load_scenarios(path)


def _scenario_label(scenario: Scenario) -> str:
    route = scenario.expected_route.value.replace("_", " ").title()
    approval = " · Approval" if scenario.requires_approval else ""
    return f"{scenario.id} · {route}{approval} · {scenario.query}"


def _thread_id_for(scenario: Scenario, run_number: int) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", scenario.id).strip("-")
    return f"streamlit-{safe_id}-{run_number}"


def _extract_interrupt(payload: dict[str, Any]) -> Any | None:
    interrupts = payload.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def _run_selected_scenario(scenario: Scenario) -> None:
    run_number = int(st.session_state.get("run_number", 0)) + 1
    st.session_state.run_number = run_number
    st.session_state.active_scenario_id = scenario.id
    st.session_state.active_thread_id = _thread_id_for(scenario, run_number)
    config = {"configurable": {"thread_id": st.session_state.active_thread_id}}
    st.session_state.last_payload = st.session_state.graph.invoke(
        initial_state(scenario),
        config=config,
    )


def _resume(decision: dict[str, Any]) -> None:
    config = {"configurable": {"thread_id": st.session_state.active_thread_id}}
    st.session_state.last_payload = st.session_state.graph.invoke(
        Command(resume=decision),
        config=config,
    )


def _route_badge(route: Route) -> str:
    labels = {
        Route.SIMPLE: "Safe answer",
        Route.TOOL: "Mock tool lookup",
        Route.MISSING_INFO: "Needs clarification",
        Route.RISKY: "Needs approval",
        Route.ERROR: "Retry path",
        Route.DEAD_LETTER: "Dead letter",
        Route.DONE: "Done",
    }
    return labels.get(route, route.value)


def _compact_status(label: str, value: str) -> str:
    """Render a compact status card without st.metric truncation."""
    return (
        '<div class="summary-card">'
        f'<div class="summary-label">{html.escape(label)}</div>'
        f'<div class="summary-value">{html.escape(value)}</div>'
        '</div>'
    )


def _render_summary_cards(items: list[tuple[str, str]]) -> None:
    cards = "".join(_compact_status(label, value) for label, value in items)
    st.markdown(f'<div class="summary-grid">{cards}</div>', unsafe_allow_html=True)


def _render_events(payload: dict[str, Any]) -> None:
    events = payload.get("events") or []
    if not events:
        st.info("No audit events were returned.")
        return

    rows = [
        {
            "Step": index,
            "Node": event.get("node", ""),
            "Status": event.get("event_type", ""),
            "Message": event.get("message", ""),
        }
        for index, event in enumerate(events, start=1)
    ]
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_final_state(payload: dict[str, Any]) -> None:
    st.subheader("Result")
    route = payload.get("route", "unknown")
    final_answer = (
        payload.get("final_answer")
        or payload.get("pending_question")
        or "Workflow completed."
    )

    approval = payload.get("approval") or {}
    if approval:
        approval_status = "Approved" if approval.get("approved") else "Rejected"
    else:
        approval_status = "No HITL"
    _render_summary_cards(
        [
            ("Route", str(route).replace("_", " ").title()),
            ("Retries", str(int(payload.get("attempt", 0)))),
            ("Approval", approval_status),
        ]
    )

    if route == Route.MISSING_INFO.value or payload.get("pending_question"):
        st.warning(final_answer)
    elif payload.get("errors"):
        st.error(final_answer)
    else:
        st.success(final_answer)

    with st.expander("Audit trail", expanded=True):
        _render_events(payload)

    tool_results = payload.get("tool_results") or []
    if tool_results:
        with st.expander("Mock tool evidence"):
            for result in tool_results:
                st.code(result)


st.set_page_config(page_title="LangGraph HITL Demo", page_icon="✅", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 2rem;}
    div[data-testid="stMetric"] {
        background: rgba(250, 250, 250, 0.05);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 1rem;
        padding: 1rem;
    }
    .ticket-card {
        border: 1px solid rgba(128, 128, 128, 0.25);
        border-radius: 1rem;
        padding: 1rem 1.25rem;
        margin: 0.5rem 0 1rem 0;
        background: rgba(128, 128, 128, 0.06);
    }
    .summary-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .summary-card {
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 0.85rem;
        padding: 0.65rem 0.8rem;
        background: rgba(128, 128, 128, 0.055);
        min-width: 0;
    }
    .summary-label {
        color: rgba(128, 128, 128, 0.95);
        font-size: 0.72rem;
        line-height: 1.1;
        margin-bottom: 0.25rem;
    }
    .summary-value {
        font-size: 0.9rem;
        font-weight: 650;
        line-height: 1.2;
        overflow-wrap: anywhere;
    }
    @media (max-width: 900px) {
        .summary-grid {grid-template-columns: 1fr;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "graph" not in st.session_state:
    st.session_state.graph = _build_app_graph()
if "last_payload" not in st.session_state:
    st.session_state.last_payload = None
if "run_number" not in st.session_state:
    st.session_state.run_number = 0
if "active_scenario_id" not in st.session_state:
    st.session_state.active_scenario_id = None
if "active_thread_id" not in st.session_state:
    st.session_state.active_thread_id = None

scenarios = _load_demo_scenarios()
scenario_lookup = {scenario.id: scenario for scenario in scenarios}

st.title("LangGraph Support Workflow Demo")
st.caption(
    "Choose a prepared mock ticket, run the graph, then approve or reject only when the "
    "workflow reaches the human-in-the-loop step."
)

left, right = st.columns([1.05, 1], gap="large")

with left:
    selected_id = st.selectbox(
        "Mock support ticket",
        options=list(scenario_lookup),
        format_func=lambda scenario_id: _scenario_label(scenario_lookup[scenario_id]),
        help="These options come from the repo's sample scenario data; no manual typing is needed.",
    )
    selected_scenario = scenario_lookup[selected_id]

    st.markdown(
        f"""
        <div class="ticket-card">
            <strong>Selected ticket</strong><br>
            {html.escape(selected_scenario.query)}<br><br>
            <small><strong>Expected path:</strong>
            {html.escape(_route_badge(selected_scenario.expected_route))}</small>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_summary_cards(
        [
            ("Scenario", selected_scenario.id),
            ("Route", selected_scenario.expected_route.value.replace("_", " ").title()),
            ("Approval", "HITL" if selected_scenario.requires_approval else "No HITL"),
        ]
    )

    if st.button("Run selected ticket", type="primary", width="stretch"):
        try:
            _run_selected_scenario(selected_scenario)
        except Exception as exc:  # pragma: no cover - UI safety net
            st.session_state.last_payload = None
            st.error(f"The graph run failed: {exc}")

with right:
    payload = st.session_state.last_payload
    selected_is_active = st.session_state.active_scenario_id == selected_id

    if not payload or not selected_is_active:
        st.info("Run the selected mock ticket to see the workflow result here.")
    else:
        interrupt_payload = _extract_interrupt(payload)
        if interrupt_payload is not None:
            st.subheader("Approval required")
            st.warning("This ticket paused at the human approval step.")
            st.json(interrupt_payload)

            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button("Approve action", type="primary", width="stretch"):
                    try:
                        _resume(
                            {
                                "approved": True,
                                "reviewer": "streamlit-reviewer",
                                "comment": "Approved from Streamlit UI",
                            }
                        )
                        st.rerun()
                    except Exception as exc:  # pragma: no cover - UI safety net
                        st.error(f"Approval resume failed: {exc}")
            with reject_col:
                if st.button("Reject action", width="stretch"):
                    try:
                        _resume(
                            {
                                "approved": False,
                                "reviewer": "streamlit-reviewer",
                                "comment": "Rejected from Streamlit UI",
                            }
                        )
                        st.rerun()
                    except Exception as exc:  # pragma: no cover - UI safety net
                        st.error(f"Rejection resume failed: {exc}")
        else:
            _render_final_state(payload)
