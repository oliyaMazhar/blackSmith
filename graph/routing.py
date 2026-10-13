"""Conditional edge routing logic for the BlackSmith LangGraph workflow (§10).

Each router function examines WorkflowState and returns the name of the next
node to execute. Clear, explicit routing prevents ambiguous workflow paths.
"""

from __future__ import annotations

import logging
from typing import Literal

from graph.state import WorkflowState

logger = logging.getLogger(__name__)

# Maximum tool call iterations to prevent infinite loops
_DEFAULT_MAX_ITERATIONS = 8


def route_after_intake(
    state: WorkflowState,
) -> Literal["classify", "end_failed"]:
    """Route after intake: proceed to classify or abort on fatal error."""
    if state.get("workflow_status") == "failed":
        logger.warning("[route] Intake failed; routing to end_failed.")
        return "end_failed"
    return "classify"


def route_after_classify(
    state: WorkflowState,
) -> Literal["agent_reason", "end_failed"]:
    """Route after classification: always proceed to reasoning unless fatal."""
    if state.get("workflow_status") == "failed":
        return "end_failed"
    return "agent_reason"


def route_after_agent_reason(
    state: WorkflowState,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
) -> Literal["execute_tools", "draft_response", "end_failed"]:
    """Route after agent reasoning step.

    - If agent proposed tool calls → execute_tools (governed boundary).
    - If agent produced a direct draft → draft_response.
    - If error or max iterations exceeded → end_failed.
    """
    if state.get("workflow_status") == "failed" or state.get("error"):
        return "end_failed"

    iteration = state.get("tool_iteration_count", 0)
    if iteration >= max_iterations:
        logger.warning(
            "[route] Maximum tool iterations (%d) reached. Forcing draft.", max_iterations
        )
        return "draft_response"

    pending = state.get("pending_tool_calls", [])
    if pending:
        return "execute_tools"

    # draft is already set or agent produced text without tool calls
    return "draft_response"


def route_after_execute_tools(
    state: WorkflowState,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
) -> Literal["agent_reason", "draft_response", "end_failed"]:
    """Route after tool execution.

    Returns to agent_reason for another reasoning cycle, unless:
    - Maximum iterations exceeded → draft_response.
    - Fatal error in state → end_failed.
    """
    if state.get("workflow_status") == "failed" or state.get("error"):
        return "end_failed"

    iteration = state.get("tool_iteration_count", 0)
    if iteration >= max_iterations:
        logger.warning(
            "[route] Maximum iterations (%d) reached after tool execution. Forcing draft.", max_iterations
        )
        return "draft_response"

    return "agent_reason"


def route_after_draft(
    state: WorkflowState,
) -> Literal["check_response", "end_failed"]:
    """Route after draft: check the response unless fatally failed."""
    if state.get("workflow_status") == "failed" or state.get("error"):
        return "end_failed"
    draft = state.get("response_draft")
    if not draft:
        logger.warning("[route] No draft produced; routing to end_failed.")
        return "end_failed"
    return "check_response"


def route_after_check(
    state: WorkflowState,
) -> Literal["persist", "end_failed"]:
    """Route after consistency check: always persist (warnings recorded, not fatal)."""
    if state.get("workflow_status") == "failed" or state.get("error"):
        return "end_failed"
    return "persist"
