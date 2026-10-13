"""Pipeline runner for BlackSmith customer support workflow.

Provides the high-level run_support_ticket() function that:
1. Accepts a customer message.
2. Builds initial state.
3. Invokes the compiled LangGraph.
4. Returns a structured result summary.

Used by main.py and integration tests.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from graph.state import WorkflowState, make_initial_state

logger = logging.getLogger(__name__)


@dataclass
class TicketRunResult:
    """Summary of a completed support ticket workflow run.

    Provides the information needed for terminal display and testing.
    """
    run_id: str
    ticket_id: Optional[int]
    workflow_status: str

    # Classification
    category: str = "UNKNOWN"
    sentiment: str = "NEUTRAL"
    priority: str = "MEDIUM"
    classification_reasoning: str = ""
    classification_confidence: float = 0.0

    # Context retrieved
    customer_id: Optional[int] = None
    order_id: Optional[int] = None
    retrieved_context: Dict[str, Any] = field(default_factory=dict)

    # Tool execution summary
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    governance_decisions: List[str] = field(default_factory=list)  # decision mode strings

    # Governance summary
    risk_scores: List[int] = field(default_factory=list)
    governance_explanations: List[str] = field(default_factory=list)

    # Response
    response_draft: Optional[str] = None
    ticket_saved: bool = False
    draft_saved: bool = False

    # Issues
    validation_errors: List[str] = field(default_factory=list)
    error: Optional[str] = None


def run_support_ticket(
    ticket_body: str,
    ticket_subject: Optional[str] = None,
    customer_id: Optional[int] = None,
    order_id: Optional[int] = None,
    graph: Optional[Any] = None,
    approval_handler: Optional[Any] = None,
    run_id: Optional[str] = None,
) -> TicketRunResult:
    """Run a customer support ticket through the complete governed workflow.

    Args:
        ticket_body: Customer complaint text.
        ticket_subject: Optional subject line.
        customer_id: Customer ID if known in advance.
        order_id: Order ID if known in advance.
        graph: Pre-compiled LangGraph app. Built fresh if None.
        approval_handler: Approval handler for ASSISTED decisions. Terminal if None.
        run_id: Optional run identifier. Generated if None.

    Returns:
        TicketRunResult with all workflow outputs.
    """
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    logger.info("[runner] Starting ticket run: run_id=%s", run_id)

    # Build initial state
    initial_state = make_initial_state(
        run_id=run_id,
        ticket_body=ticket_body,
        ticket_subject=ticket_subject,
        customer_id=customer_id,
        order_id=order_id,
    )

    # Get or build graph
    if graph is None:
        from agents.support_agent import build_support_agent
        from graph.workflow import build_support_graph
        agent = build_support_agent()
        graph = build_support_graph(agent=agent, approval_handler=approval_handler)

    # Invoke the graph
    try:
        final_state: WorkflowState = graph.invoke(initial_state)
    except Exception as e:
        logger.error("[runner] Graph invocation failed: %s", e)
        return TicketRunResult(
            run_id=run_id,
            ticket_id=None,
            workflow_status="failed",
            error=f"Graph execution failed: {e}",
        )

    # Extract and return structured result
    return _extract_result(run_id, final_state)


def _extract_result(run_id: str, state: WorkflowState) -> TicketRunResult:
    """Extract a clean result summary from the final workflow state."""
    classification = state.get("classification")
    governance_decisions = state.get("governance_decisions", [])

    return TicketRunResult(
        run_id=run_id,
        ticket_id=state.get("ticket_id"),
        workflow_status=state.get("workflow_status", "unknown"),

        category=classification.category.value if classification else "UNKNOWN",
        sentiment=classification.sentiment.value if classification else "NEUTRAL",
        priority=classification.priority.value if classification else "MEDIUM",
        classification_reasoning=classification.reasoning if classification else "",
        classification_confidence=classification.confidence if classification else 0.0,

        customer_id=state.get("customer_id"),
        order_id=state.get("order_id"),
        retrieved_context=dict(state.get("retrieved_context", {})),

        tool_results=list(state.get("tool_results", [])),
        governance_decisions=[d.decision.value for d in governance_decisions],
        risk_scores=[
            int(ra.overall_score * 100)
            for ra in state.get("risk_assessments", [])
        ],
        governance_explanations=[
            d.explanation for d in governance_decisions
        ],

        response_draft=state.get("response_draft"),
        ticket_saved=bool(state.get("ticket_saved")),
        draft_saved=bool(state.get("draft_saved")),
        validation_errors=list(state.get("validation_errors", [])),
        error=state.get("error"),
    )
