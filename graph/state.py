"""LangGraph runtime state for BlackSmith support workflow (§5.2).

Defines ONE canonical runtime state type used across the entire graph.
Distinct from domain models — holds execution-time context across nodes.

Uses TypedDict with Annotated fields for LangGraph state reducers where needed.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Sequence
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from core.models import (
    GovernanceDecision,
    HumanApprovalResult,
    RiskAssessment,
    TicketClassification,
    ToolExecutionResult,
)


class WorkflowState(TypedDict, total=False):
    """Canonical LangGraph runtime state for one support ticket run.

    Fields are Optional so nodes can update them incrementally without
    requiring all keys to be set at graph entry.

    Reducers:
    - messages: Uses add_messages reducer to append, not overwrite.
    - tool_results: Appends new results each iteration.
    - governance_decisions: Accumulates governance decisions per tool call.
    """

    # -----------------------------------------------------------------------
    # Run identity
    # -----------------------------------------------------------------------
    run_id: str
    """Unique identifier for this workflow execution."""

    # -----------------------------------------------------------------------
    # Ticket context
    # -----------------------------------------------------------------------
    ticket_id: Optional[int]
    """Persisted support ticket ID (set after intake)."""

    ticket_body: str
    """Raw customer message / complaint text."""

    ticket_subject: Optional[str]
    """Optional subject line from customer."""

    customer_id: Optional[int]
    """Customer ID if known from ticket or lookup."""

    order_id: Optional[int]
    """Order ID if known from ticket or lookup."""

    # -----------------------------------------------------------------------
    # LLM Conversation messages
    # -----------------------------------------------------------------------
    messages: Annotated[Sequence[BaseMessage], add_messages]
    """Full conversation thread including system, human, AI, and tool messages."""

    # -----------------------------------------------------------------------
    # Classification
    # -----------------------------------------------------------------------
    classification: Optional[TicketClassification]
    """Structured classification result: category, sentiment, priority."""

    # -----------------------------------------------------------------------
    # Retrieved business context (human-readable summaries)
    # -----------------------------------------------------------------------
    retrieved_context: Dict[str, Any]
    """Key-value summaries of retrieved customer, order, refund context."""

    # -----------------------------------------------------------------------
    # Tool proposals and execution
    # -----------------------------------------------------------------------
    pending_tool_calls: List[Dict[str, Any]]
    """Tool calls proposed by the LLM in the most recent agent turn."""

    tool_results: Annotated[List[Dict[str, Any]], lambda a, b: a + b]
    """Accumulated results from all tool executions this run."""

    tool_iteration_count: int
    """Counter to enforce max tool iteration limits."""

    # -----------------------------------------------------------------------
    # Governance tracking
    # -----------------------------------------------------------------------
    risk_assessments: List[RiskAssessment]
    """Risk assessments calculated for this run's tool calls."""

    governance_decisions: List[GovernanceDecision]
    """Governance decisions (AUTO/ASSISTED/HALTED) for each proposed call."""

    human_approvals: List[HumanApprovalResult]
    """Human approval results for ASSISTED-mode tool calls."""

    # -----------------------------------------------------------------------
    # Response drafting
    # -----------------------------------------------------------------------
    response_draft: Optional[str]
    """Final drafted response to the customer."""

    # -----------------------------------------------------------------------
    # Validation and errors
    # -----------------------------------------------------------------------
    validation_errors: List[str]
    """Non-fatal validation issues encountered during the run."""

    # -----------------------------------------------------------------------
    # Workflow status and control
    # -----------------------------------------------------------------------
    workflow_status: str
    """
    Current workflow phase:
      intake | classifying | retrieving | reasoning | executing |
      drafting | checking | persisting | completed | failed
    """

    error: Optional[str]
    """Fatal error message if the workflow cannot continue."""

    # -----------------------------------------------------------------------
    # Persistence tracking
    # -----------------------------------------------------------------------
    ticket_saved: bool
    """True after the ticket record was persisted to PostgreSQL."""

    draft_saved: bool
    """True after the response draft was saved to the messages table."""

    audit_ids: List[int]
    """Audit record IDs created during this run."""


def make_initial_state(
    run_id: str,
    ticket_body: str,
    ticket_subject: Optional[str] = None,
    customer_id: Optional[int] = None,
    order_id: Optional[int] = None,
) -> WorkflowState:
    """Create a clean initial WorkflowState for a new run."""
    return WorkflowState(
        run_id=run_id,
        ticket_id=None,
        ticket_body=ticket_body,
        ticket_subject=ticket_subject,
        customer_id=customer_id,
        order_id=order_id,
        messages=[],
        classification=None,
        retrieved_context={},
        pending_tool_calls=[],
        tool_results=[],
        tool_iteration_count=0,
        risk_assessments=[],
        governance_decisions=[],
        human_approvals=[],
        response_draft=None,
        validation_errors=[],
        workflow_status="intake",
        error=None,
        ticket_saved=False,
        draft_saved=False,
        audit_ids=[],
    )
