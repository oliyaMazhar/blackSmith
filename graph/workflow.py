"""LangGraph workflow construction for BlackSmith (§10).

Builds and compiles the complete state graph:
  intake → classify → agent_reason ⟷ execute_tools
                                  ↓
                            draft_response → check_response → persist → END

All conditional routing is defined explicitly.
The executed graph is the only entry point into the governed workflow.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.graph import StateGraph, END

from agents.support_agent import SupportAgent
from core.config import AppConfig, get_config
from graph.nodes import (
    node_check_response,
    node_classify,
    node_draft_response,
    node_execute_tools,
    node_intake,
    node_persist,
    node_agent_reason,
)
from graph.routing import (
    route_after_agent_reason,
    route_after_check,
    route_after_classify,
    route_after_draft,
    route_after_execute_tools,
    route_after_intake,
)
from graph.state import WorkflowState

logger = logging.getLogger(__name__)


def build_support_graph(
    agent: SupportAgent,
    approval_handler: Optional[Any] = None,
    config: Optional[AppConfig] = None,
) -> Any:
    """Build and compile the BlackSmith customer support LangGraph workflow.

    Args:
        agent: Initialized SupportAgent (from build_support_agent).
        approval_handler: Optional approval handler for ASSISTED governance decisions.
                          Defaults to TerminalApprovalHandler if None.
        config: Application configuration.

    Returns:
        Compiled LangGraph app (CompiledGraph) ready for invocation.
    """
    max_iterations = 8
    if config is not None:
        max_iterations = config.max_tool_iterations
    else:
        try:
            cfg = get_config()
            max_iterations = cfg.max_tool_iterations
        except Exception:
            max_iterations = 8

    # ------------------------------------------------------------------
    # Build the state graph
    # ------------------------------------------------------------------
    graph = StateGraph(WorkflowState)

    # Add all nodes -  each bound to closure carrying the agent/config
    graph.add_node("intake", node_intake)

    graph.add_node(
        "classify",
        lambda state: node_classify(state, agent=agent),
    )

    graph.add_node(
        "agent_reason",
        lambda state: node_agent_reason(state, agent=agent),
    )

    graph.add_node(
        "execute_tools",
        lambda state: node_execute_tools(state, approval_handler=approval_handler),
    )

    graph.add_node(
        "draft_response",
        lambda state: node_draft_response(state, agent=agent),
    )

    graph.add_node("check_response", node_check_response)

    graph.add_node("persist", node_persist)

    # Terminal error node — no outgoing edges → reaches END
    graph.add_node(
        "end_failed",
        lambda state: _handle_failure(state),
    )

    # ------------------------------------------------------------------
    # Set entry point
    # ------------------------------------------------------------------
    graph.set_entry_point("intake")

    # ------------------------------------------------------------------
    # Add conditional edges
    # ------------------------------------------------------------------
    graph.add_conditional_edges(
        "intake",
        route_after_intake,
        {"classify": "classify", "end_failed": "end_failed"},
    )

    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {"agent_reason": "agent_reason", "end_failed": "end_failed"},
    )

    graph.add_conditional_edges(
        "agent_reason",
        lambda state: route_after_agent_reason(state, max_iterations=max_iterations),
        {
            "execute_tools": "execute_tools",
            "draft_response": "draft_response",
            "end_failed": "end_failed",
        },
    )

    graph.add_conditional_edges(
        "execute_tools",
        lambda state: route_after_execute_tools(state, max_iterations=max_iterations),
        {
            "agent_reason": "agent_reason",
            "draft_response": "draft_response",
            "end_failed": "end_failed",
        },
    )

    graph.add_conditional_edges(
        "draft_response",
        route_after_draft,
        {"check_response": "check_response", "end_failed": "end_failed"},
    )

    graph.add_conditional_edges(
        "check_response",
        route_after_check,
        {"persist": "persist", "end_failed": "end_failed"},
    )

    # Terminal edges
    graph.add_edge("persist", END)
    graph.add_edge("end_failed", END)

    # ------------------------------------------------------------------
    # Compile
    # ------------------------------------------------------------------
    compiled = graph.compile()
    logger.info("BlackSmith support graph compiled successfully.")
    return compiled


def _handle_failure(state: WorkflowState) -> dict:
    """Terminal failure handler: log and persist audit information."""
    error = state.get("error", "Unknown workflow failure")
    run_id = state.get("run_id", "unknown")
    logger.error("[end_failed] run_id=%s | %s", run_id, error)

    # Attempt to update ticket status to HALTED
    ticket_id = state.get("ticket_id")
    if ticket_id:
        try:
            from database.connection import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE support_tickets SET workflow_status='HALTED', updated_at=NOW() WHERE ticket_id=%s",
                        (ticket_id,),
                    )
                conn.commit()
        except Exception as db_err:
            logger.error("[end_failed] Failed to update ticket status: %s", db_err)

    return {"workflow_status": "failed"}
