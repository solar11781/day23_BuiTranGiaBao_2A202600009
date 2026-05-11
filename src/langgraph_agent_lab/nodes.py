"""Node implementations for the LangGraph workflow.

Each function is small, testable, and returns a partial state update. The nodes avoid
mutating the input state in place so LangGraph reducers can merge append-only fields
predictably.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .state import AgentState, ApprovalDecision, Route, make_event

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Return lower-cased word tokens for word-boundary keyword matching."""
    return set(_WORD_RE.findall(text.lower()))


def intake_node(state: AgentState) -> dict[str, Any]:
    """Normalize the raw support-ticket query and record a first audit event."""
    query = " ".join(state.get("query", "").strip().split())
    metadata: dict[str, Any] = {
        "original_length": len(state.get("query", "")),
        "normalized_length": len(query),
        "word_count": len(_WORD_RE.findall(query.lower())),
    }
    return {
        "query": query,
        "messages": [f"intake:{query[:80]}"],
        "events": [make_event("intake", "completed", "query normalized", **metadata)],
    }


def classify_node(state: AgentState) -> dict[str, Any]:
    """Classify the query into the lab's required route set.

    The policy follows the lab README priority order and uses keyword/state logic rather
    than scenario IDs: risky > tool > missing_info > error > simple.
    """
    query = state.get("query", "")
    words = _tokens(query)
    word_count = len(_WORD_RE.findall(query.lower()))

    risky_keywords = {"refund", "delete", "send", "cancel", "remove", "revoke"}
    tool_keywords = {"status", "order", "lookup", "check", "track", "find", "search"}
    vague_pronouns = {"it", "this", "that", "thing", "issue", "problem"}
    error_keywords = {"timeout", "fail", "failure", "error", "crash", "unavailable", "recover"}

    route = Route.SIMPLE
    risk_level = "low"
    reason = "default_simple"

    if words & risky_keywords:
        route = Route.RISKY
        risk_level = "high"
        reason = "risky_keyword"
    elif words & tool_keywords:
        route = Route.TOOL
        reason = "tool_keyword"
    elif word_count < 5 and words & vague_pronouns:
        route = Route.MISSING_INFO
        reason = "short_vague_query"
    elif words & error_keywords:
        route = Route.ERROR
        risk_level = "medium"
        reason = "error_keyword"

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [
            make_event(
                "classify",
                "completed",
                f"route={route.value}",
                reason=reason,
                matched_keywords=sorted(
                    words & (risky_keywords | tool_keywords | vague_pronouns | error_keywords)
                ),
            )
        ],
    }


def ask_clarification_node(state: AgentState) -> dict[str, Any]:
    """Ask for missing information instead of fabricating an answer."""
    route = state.get("route")
    if route == Route.RISKY.value and state.get("approval"):
        question = (
            "The proposed action was not approved. "
            "Please provide revised instructions or approval context."
        )
    else:
        question = (
            "Can you provide the account, order, or request details needed "
            "to handle this ticket?"
        )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict[str, Any]:
    """Dispatch mock tool work to parallel branch nodes.

    The actual mock evidence is produced by ``account_lookup_node`` and
    ``policy_lookup_node``. Keeping this dispatch node named ``tool`` preserves the
    original graph shape while enabling a Send()-based fan-out/fan-in extension.
    """
    attempt = int(state.get("attempt", 0))
    return {
        "events": [
            make_event(
                "tool",
                "fanout_started",
                "parallel mock tools dispatched",
                attempt=attempt,
                parallel_tools=["account_tool", "policy_tool"],
            )
        ],
    }


def _mock_tool_result(state: AgentState, tool_name: str, evidence_type: str) -> dict[str, Any]:
    """Return one mock tool branch result for the current attempt."""
    attempt = int(state.get("attempt", 0))
    route = state.get("route")
    scenario_id = state.get("scenario_id", "unknown")
    should_retry = bool(state.get("should_retry", route == Route.ERROR.value))

    if should_retry and attempt < 2:
        status = "error"
        result = (
            f"ERROR: {evidence_type} unavailable attempt={attempt} "
            f"scenario={scenario_id}"
        )
    else:
        status = "ok"
        result = (
            f"OK: {evidence_type} evidence route={route} "
            f"attempt={attempt} scenario={scenario_id}"
        )

    return {
        "tool_results": [result],
        "events": [
            make_event(
                tool_name,
                "completed",
                f"{evidence_type} tool completed attempt={attempt}",
                attempt=attempt,
                status=status,
                evidence_type=evidence_type,
            )
        ],
    }


def account_lookup_node(state: AgentState) -> dict[str, Any]:
    """Parallel mock tool branch for account/order evidence."""
    return _mock_tool_result(state, "account_tool", "account/order")


def policy_lookup_node(state: AgentState) -> dict[str, Any]:
    """Parallel mock tool branch for policy/safety evidence."""
    return _mock_tool_result(state, "policy_tool", "policy/safety")


def risky_action_node(state: AgentState) -> dict[str, Any]:
    """Prepare a risky action package for human approval."""
    query = state.get("query", "")
    proposed_action = f"Review and approve before executing risky support request: {query}"
    return {
        "proposed_action": proposed_action,
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required",
                risk_level=state.get("risk_level", "high"),
            )
        ],
    }


def approval_node(state: AgentState) -> dict[str, Any]:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos. The default path
    uses a mock approval so tests and CI run offline.
    """
    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt(
            {
                "proposed_action": state.get("proposed_action"),
                "risk_level": state.get("risk_level"),
                "query": state.get("query"),
                "instructions": (
                    "Approve or reject with {'approved': true/false, "
                    "'reviewer': 'name', 'comment': '...'}."
                ),
            }
        )
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(
                approved=bool(value),
                comment="resumed from boolean decision",
            )
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")

    return {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"approved={decision.approved}",
                reviewer=decision.reviewer,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict[str, Any]:
    """Record one bounded retry attempt and expose backoff metadata for auditability."""
    attempt = int(state.get("attempt", 0)) + 1
    max_attempts = int(state.get("max_attempts", 3))
    backoff_seconds = min(2 ** max(attempt - 1, 0), 30)
    exhausted = attempt >= max_attempts
    error = f"retry attempt={attempt} max_attempts={max_attempts} exhausted={exhausted}"
    return {
        "attempt": attempt,
        "errors": [error],
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                exhausted=exhausted,
            )
        ],
    }


def answer_node(state: AgentState) -> dict[str, Any]:
    """Produce a final response grounded in route, tool evidence, and approval status."""
    route = state.get("route")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval") or {}

    if route == Route.RISKY.value:
        evidence = "; ".join(tool_results[-2:]) if tool_results else "no tool evidence returned"
        answer = (
            "Approved risky workflow completed. "
            f"Reviewer={approval.get('reviewer', 'unknown')}. Evidence: {evidence}"
        )
    elif tool_results:
        evidence = "; ".join(tool_results[-2:])
        answer = f"I found the requested support information. Evidence: {evidence}"
    else:
        answer = "Here is a safe support response based on the supplied request."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated", route=route)],
    }


def evaluate_node(state: AgentState) -> dict[str, Any]:
    """Evaluate current-attempt tool results — the 'done?' check for retry loops."""
    attempt = int(state.get("attempt", 0))
    events = state.get("events", []) or []
    current_tool_events = [
        event
        for event in events
        if event.get("node") in {"account_tool", "policy_tool"}
        and (event.get("metadata") or {}).get("attempt") == attempt
    ]
    failed = any(
        (event.get("metadata") or {}).get("status") == "error"
        for event in current_tool_events
    )

    if not current_tool_events:
        tool_results = state.get("tool_results", [])
        latest = tool_results[-1] if tool_results else ""
        failed = latest.startswith("ERROR:")

    evaluation_result = "needs_retry" if failed else "success"
    return {
        "evaluation_result": evaluation_result,
        "events": [
            make_event(
                "evaluate",
                "completed",
                (
                    "current tool evidence indicates failure, retry needed"
                    if failed
                    else "current tool evidence satisfactory"
                ),
                attempt=attempt,
                branch_count=len(current_tool_events),
                failed=failed,
            )
        ],
    }


def dead_letter_node(state: AgentState) -> dict[str, Any]:
    """Log unresolvable failures for manual review after retry exhaustion."""
    attempt = int(state.get("attempt", 0))
    message = (
        "Request could not be completed after maximum retry attempts. "
        "Logged for manual review."
    )
    return {
        "final_answer": message,
        "errors": [f"dead_letter scenario={state.get('scenario_id', 'unknown')} attempt={attempt}"],
        "events": [make_event("dead_letter", "completed", "max retries exceeded", attempt=attempt)],
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
