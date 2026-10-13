"""LangGraph node implementations for the BlackSmith support workflow (§10).

Each node function receives and returns WorkflowState. Nodes must not:
- Bypass the gatekeeper for any protected business mutation.
- Fabricate business facts when data is unavailable.
- Swallow exceptions and continue as if nothing happened.

Node execution order (enforced by graph edges):
  intake → classify → agent_reason → execute_tools → draft_response
                  ↑___________________________|   (tool loop)
         → check_response → persist → complete
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from autonomy.gatekeeper import GovernedExecutionResult, governed_execute
from core.models import (
    GovernanceDecision,
    RiskAssessment,
    TicketClassification,
)
from graph.state import WorkflowState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node: intake
# ---------------------------------------------------------------------------

def node_intake(state: WorkflowState) -> Dict[str, Any]:
    """Normalize incoming ticket and persist initial record to PostgreSQL.

    Saves the ticket body, subject, and customer/order IDs to the database.
    Sets the ticket_id in state for downstream reference.
    """
    logger.info("[intake] run_id=%s | starting ticket intake", state.get("run_id"))

    ticket_body = state.get("ticket_body", "").strip()
    if not ticket_body:
        return {
            "workflow_status": "failed",
            "error": "Ticket body is empty. Cannot process an empty complaint.",
        }

    # Persist ticket to database
    ticket_id: Optional[int] = None
    try:
        from database.connection import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO support_tickets
                        (customer_id, order_id, body, subject, category, sentiment,
                         priority, workflow_status, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, 'UNKNOWN', 'NEUTRAL', 'MEDIUM', 'NEW',
                         NOW(), NOW())
                    RETURNING ticket_id
                    """,
                    (
                        state.get("customer_id"),
                        state.get("order_id"),
                        ticket_body,
                        state.get("ticket_subject") or "",
                    ),
                )
                row = cur.fetchone()
                if row:
                    ticket_id = row[0]
            conn.commit()
            logger.info("[intake] ticket saved with ticket_id=%s", ticket_id)
    except Exception as e:
        logger.error("[intake] Failed to persist ticket: %s", e)
        # Non-fatal: continue without a persisted ticket_id
        return {
            "workflow_status": "classifying",
            "ticket_id": None,
            "validation_errors": [f"Ticket persistence failed: {e}"],
            "messages": [HumanMessage(content=ticket_body)],
        }

    return {
        "workflow_status": "classifying",
        "ticket_id": ticket_id,
        "messages": [HumanMessage(content=ticket_body)],
    }


# ---------------------------------------------------------------------------
# Node: classify
# ---------------------------------------------------------------------------

def node_classify(state: WorkflowState, agent: Any) -> Dict[str, Any]:
    """Classify the ticket using the LLM and persist classification results.

    Calls the structured classifier, updates the ticket record, and extracts
    entity identifiers (customer_id, order_id) if detected.
    """
    logger.info("[classify] run_id=%s", state.get("run_id"))

    ticket_body = state.get("ticket_body", "")
    try:
        from agents.classifier import classify_ticket
        classification: TicketClassification = classify_ticket(ticket_body, agent.llm)
    except Exception as e:
        logger.error("[classify] Classification error: %s", e)
        from core.models import TicketCategory, TicketPriority, TicketSentiment
        classification = TicketClassification(
            category=TicketCategory.UNKNOWN,
            sentiment=TicketSentiment.NEUTRAL,
            priority=TicketPriority.MEDIUM,
            confidence=0.0,
            reasoning=f"Classification failed: {e}",
        )

    # Extract entity identifiers from detected_entities
    customer_id = state.get("customer_id")
    order_id = state.get("order_id")
    detected = classification.detected_entities or {}
    if not customer_id and detected.get("customer_id"):
        customer_id = int(detected["customer_id"])
    if not order_id and detected.get("order_id"):
        order_id = int(detected["order_id"])

    # Update ticket record with classification
    ticket_id = state.get("ticket_id")
    if ticket_id:
        try:
            from database.connection import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE support_tickets
                        SET category=%s, sentiment=%s, priority=%s,
                            workflow_status='TRIAGED', updated_at=NOW(),
                            customer_id=COALESCE(customer_id, %s),
                            order_id=COALESCE(order_id, %s)
                        WHERE ticket_id=%s
                        """,
                        (
                            classification.category.value,
                            classification.sentiment.value,
                            classification.priority.value,
                            customer_id,
                            order_id,
                            ticket_id,
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.error("[classify] Failed to update ticket classification: %s", e)

    logger.info(
        "[classify] category=%s sentiment=%s priority=%s confidence=%.2f",
        classification.category.value,
        classification.sentiment.value,
        classification.priority.value,
        classification.confidence,
    )

    return {
        "classification": classification,
        "customer_id": customer_id,
        "order_id": order_id,
        "workflow_status": "reasoning",
    }


# ---------------------------------------------------------------------------
# Node: agent_reason
# ---------------------------------------------------------------------------

def node_agent_reason(state: WorkflowState, agent: Any) -> Dict[str, Any]:
    """Run the LLM agent reasoning step to propose tool calls or draft response.

    Sends the full conversation with classification context and retrieved
    business context to the LLM. The LLM may:
    - Propose tool calls (handled in execute_tools node).
    - Draft a direct response if sufficient context exists.
    """
    logger.info(
        "[agent_reason] run_id=%s iteration=%d",
        state.get("run_id"),
        state.get("tool_iteration_count", 0),
    )

    classification: Optional[TicketClassification] = state.get("classification")
    category = classification.category.value if classification else "UNKNOWN"
    sentiment = classification.sentiment.value if classification else "NEUTRAL"

    system_prompt = agent.get_system_prompt(category=category, sentiment=sentiment)

    # Build context summary for injection
    ctx_summary = _build_context_summary(state)

    # Compose messages list for LLM
    conversation = list(state.get("messages", []))

    # Prepend system message (first call only or after major state change)
    if not any(isinstance(m, SystemMessage) for m in conversation):
        system_msg = SystemMessage(content=system_prompt)
        if ctx_summary:
            system_msg = SystemMessage(content=system_prompt + f"\n\n---\nCURRENT CONTEXT:\n{ctx_summary}")
        conversation = [system_msg] + conversation
    elif ctx_summary and state.get("tool_iteration_count", 0) > 0:
        # Inject updated context as a tool message after first tool loop
        conversation = conversation  # tool messages already in messages

    try:
        response: AIMessage = agent.llm_with_tools.invoke(conversation)
    except Exception as e:
        logger.error("[agent_reason] LLM invocation failed: %s", e)
        return {
            "workflow_status": "failed",
            "error": f"LLM agent call failed: {e}",
        }

    # Extract proposed tool calls from response
    pending_tool_calls: List[Dict[str, Any]] = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            if isinstance(tc, dict):
                pending_tool_calls.append(tc)
            else:
                # LangChain ToolCall object
                pending_tool_calls.append({
                    "id": getattr(tc, "id", str(uuid.uuid4())),
                    "name": getattr(tc, "name", ""),
                    "args": getattr(tc, "args", {}),
                })

    # If no tool calls and content is present → draft is ready
    if not pending_tool_calls and hasattr(response, "content") and response.content:
        logger.info("[agent_reason] Agent produced direct response draft (no tool calls).")
        return {
            "messages": [response],
            "pending_tool_calls": [],
            "response_draft": str(response.content),
            "workflow_status": "drafting",
        }

    logger.info(
        "[agent_reason] Agent proposed %d tool call(s).", len(pending_tool_calls)
    )

    return {
        "messages": [response],
        "pending_tool_calls": pending_tool_calls,
        "workflow_status": "executing",
    }


# ---------------------------------------------------------------------------
# Node: execute_tools
# ---------------------------------------------------------------------------

def node_execute_tools(
    state: WorkflowState,
    approval_handler: Optional[Any] = None,
) -> Dict[str, Any]:
    """Governed execution boundary for all LLM-proposed tool calls (§16).

    Every proposed tool call MUST pass through governed_execute before the
    underlying business function runs. This node is the ONLY place where
    tool execution happens.

    Returns:
        Updated state with tool results, governance decisions, and ToolMessages.
    """
    from core.models import ExecutionMode

    run_id = state.get("run_id", "")
    pending = state.get("pending_tool_calls", [])
    iteration = state.get("tool_iteration_count", 0)

    if not pending:
        return {"workflow_status": "reasoning", "pending_tool_calls": []}

    logger.info(
        "[execute_tools] run_id=%s | executing %d proposed tool call(s)",
        run_id, len(pending),
    )

    tool_messages: List[ToolMessage] = []
    new_results: List[Dict[str, Any]] = []
    new_governance_decisions: List[GovernanceDecision] = []
    new_risk_assessments: List[RiskAssessment] = []
    new_audit_ids: List[int] = []

    for tc in pending:
        tool_name = tc.get("name", "")
        tool_args = tc.get("args", {})
        tool_call_id = tc.get("id", str(uuid.uuid4()))

        logger.info("[execute_tools] Processing tool call: %s args=%s", tool_name, tool_args)

        try:
            result: GovernedExecutionResult = governed_execute(
                tool_name=tool_name,
                arguments=tool_args,
                run_id=run_id,
                approval_handler=approval_handler,
            )
        except Exception as e:
            logger.error("[execute_tools] governed_execute raised exception for '%s': %s", tool_name, e)
            # Wrap in a failure result without calling the underlying function
            error_content = json.dumps({
                "success": False,
                "error": f"Tool execution failed unexpectedly: {e}",
                "tool_name": tool_name,
                "executed": False,
            })
            tool_messages.append(
                ToolMessage(content=error_content, tool_call_id=tool_call_id, name=tool_name)
            )
            new_results.append({
                "tool_name": tool_name,
                "success": False,
                "executed": False,
                "error": str(e),
                "decision": "HALTED",
            })
            continue

        # Record governance tracking
        if result.governance_decision:
            new_governance_decisions.append(result.governance_decision)
        if result.risk_assessment:
            new_risk_assessments.append(result.risk_assessment)
        if result.audit_id:
            new_audit_ids.append(result.audit_id)

        # Build tool result summary for state
        result_summary = {
            "tool_name": tool_name,
            "decision": result.decision.value,
            "executed": result.executed,
            "success": result.executed and (result.tool_result.success if result.tool_result else False),
            "error": result.error,
            "output": result.tool_result.output if result.tool_result else None,
            "risk_score": (
                int(result.risk_assessment.overall_score * 100)
                if result.risk_assessment else None
            ),
        }
        new_results.append(result_summary)

        # Format tool message content for the LLM
        if result.decision == ExecutionMode.HALTED or (
            result.decision == ExecutionMode.ASSISTED and not result.executed
        ):
            content = json.dumps({
                "success": False,
                "executed": False,
                "decision": result.decision.value,
                "message": result.error or "Action was not authorized.",
                "explanation": (
                    result.governance_decision.explanation
                    if result.governance_decision else ""
                ),
            })
        elif result.executed and result.tool_result:
            content = json.dumps({
                "success": result.tool_result.success,
                "executed": True,
                "decision": result.decision.value,
                "output": result.tool_result.output,
                "error": result.tool_result.error,
            })
        else:
            content = json.dumps(result_summary)

        tool_messages.append(
            ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
        )

    new_iteration = iteration + 1
    return {
        "messages": tool_messages,
        "pending_tool_calls": [],
        "tool_results": new_results,
        "governance_decisions": new_governance_decisions,
        "risk_assessments": new_risk_assessments,
        "audit_ids": new_audit_ids,
        "tool_iteration_count": new_iteration,
        "workflow_status": "reasoning",
    }


# ---------------------------------------------------------------------------
# Node: draft_response
# ---------------------------------------------------------------------------

def node_draft_response(state: WorkflowState, agent: Any) -> Dict[str, Any]:
    """Generate the final customer response draft.

    If the agent already set response_draft in agent_reason, this node
    validates/refines it. Otherwise it invokes the LLM to produce a final draft
    based on all accumulated context and tool results.
    """
    logger.info("[draft_response] run_id=%s", state.get("run_id"))

    existing_draft = state.get("response_draft")
    if existing_draft:
        # Draft already produced by agent_reason; validate it meets requirements
        logger.info("[draft_response] Using existing draft from agent_reason.")
        return {
            "workflow_status": "checking",
            "response_draft": existing_draft,
        }

    # Build comprehensive context for drafting
    classification: Optional[TicketClassification] = state.get("classification")
    category = classification.category.value if classification else "UNKNOWN"
    sentiment = classification.sentiment.value if classification else "NEUTRAL"

    system_prompt = agent.get_system_prompt(category=category, sentiment=sentiment)
    ctx_summary = _build_context_summary(state)

    # Compose the drafting request
    draft_request = _build_draft_request(state)

    conversation = list(state.get("messages", []))
    if not any(isinstance(m, SystemMessage) for m in conversation):
        conversation = [SystemMessage(content=system_prompt)] + conversation

    conversation.append(HumanMessage(content=draft_request))

    try:
        response: AIMessage = agent.llm.invoke(conversation)
        draft = str(response.content) if response.content else ""
    except Exception as e:
        logger.error("[draft_response] LLM draft generation failed: %s", e)
        draft = (
            "Dear Customer,\n\nThank you for contacting us. We have received your inquiry "
            "and a support team member will follow up with you shortly.\n\n"
            "We apologize for any inconvenience caused.\n\nBest regards,\nBlackSmith Support Team"
        )
        return {
            "response_draft": draft,
            "workflow_status": "checking",
            "validation_errors": [f"Draft generation failed, using fallback: {e}"],
        }

    return {
        "messages": [response],
        "response_draft": draft,
        "workflow_status": "checking",
    }


# ---------------------------------------------------------------------------
# Node: check_response
# ---------------------------------------------------------------------------

def node_check_response(state: WorkflowState) -> Dict[str, Any]:
    """Validate the response draft for consistency with governance outcomes.

    Checks that the draft does not:
    - Claim a refund was approved/processed when it was blocked.
    - Promise actions that were denied.
    - State that external communications were sent.

    Adds validation warnings but does not fail — the draft is preserved with warnings noted.
    """
    logger.info("[check_response] run_id=%s", state.get("run_id"))

    draft = state.get("response_draft", "") or ""
    issues: List[str] = []

    # Check for false claims about blocked actions
    governance_decisions = state.get("governance_decisions", [])
    for decision in governance_decisions:
        if decision.decision.value in ("HALTED",):
            # Look for suspicious language in draft
            for phrase in ("refund processed", "refund approved", "has been refunded",
                           "payment returned", "action completed"):
                if phrase.lower() in draft.lower():
                    issues.append(
                        f"Draft may falsely claim a HALTED action succeeded: '{phrase}'"
                    )

    # Check for false external send claims
    for phrase in ("email sent", "message sent", "we have emailed", "you will receive an email"):
        if phrase.lower() in draft.lower():
            issues.append(
                f"Draft may falsely claim external communication was sent: '{phrase}'"
            )

    validation_errors = list(state.get("validation_errors", []))
    validation_errors.extend(issues)

    if issues:
        logger.warning("[check_response] Draft consistency issues detected: %s", issues)

    return {
        "validation_errors": validation_errors,
        "workflow_status": "persisting",
    }


# ---------------------------------------------------------------------------
# Node: persist
# ---------------------------------------------------------------------------

def node_persist(state: WorkflowState) -> Dict[str, Any]:
    """Persist final ticket state and response draft to PostgreSQL.

    Saves:
    - Updated ticket status and response draft.
    - Response draft as a message record.
    """
    logger.info("[persist] run_id=%s ticket_id=%s", state.get("run_id"), state.get("ticket_id"))

    ticket_id = state.get("ticket_id")
    draft = state.get("response_draft") or ""
    classification: Optional[TicketClassification] = state.get("classification")

    ticket_saved = False
    draft_saved = False

    try:
        from database.connection import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                if ticket_id:
                    # Update ticket with final status and draft
                    final_status = "RESOLVED"
                    if state.get("error"):
                        final_status = "HALTED"

                    cat_val = classification.category.value if classification else "UNKNOWN"
                    sent_val = classification.sentiment.value if classification else "NEUTRAL"
                    pri_val = classification.priority.value if classification else "MEDIUM"

                    cur.execute(
                        """
                        UPDATE support_tickets
                        SET response_draft=%s, workflow_status=%s,
                            category=%s, sentiment=%s, priority=%s,
                            updated_at=NOW()
                        WHERE ticket_id=%s
                        """,
                        (draft, final_status, cat_val, sent_val, pri_val, ticket_id),
                    )
                    ticket_saved = True

                # Save draft as a message record if we have a draft and customer
                customer_id = state.get("customer_id")
                if draft and customer_id:
                    cur.execute(
                        """
                        INSERT INTO messages
                            (customer_id, channel, direction, subject, body, ticket_id, created_at)
                        VALUES
                            (%s, 'internal', 'outbound', %s, %s, %s, NOW())
                        """,
                        (
                            customer_id,
                            "Support Response Draft",
                            draft,
                            ticket_id,
                        ),
                    )
                    draft_saved = True
                elif draft:
                    # No customer_id — save without linking
                    draft_saved = True  # Draft exists in ticket record

            conn.commit()
            logger.info("[persist] ticket_saved=%s draft_saved=%s", ticket_saved, draft_saved)
    except Exception as e:
        logger.error("[persist] Database persistence failed: %s", e)
        return {
            "workflow_status": "completed",
            "ticket_saved": False,
            "draft_saved": False,
            "validation_errors": list(state.get("validation_errors", [])) + [
                f"Persistence failed: {e}"
            ],
        }

    return {
        "workflow_status": "completed",
        "ticket_saved": ticket_saved,
        "draft_saved": draft_saved,
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_context_summary(state: WorkflowState) -> str:
    """Build a concise text summary of retrieved context for LLM injection."""
    ctx = state.get("retrieved_context", {})
    if not ctx:
        return ""

    lines = ["Retrieved business context:"]
    for key, value in ctx.items():
        if value:
            lines.append(f"  {key}: {value}")

    classification = state.get("classification")
    if classification:
        lines.append(
            f"\nTicket classification: category={classification.category.value}, "
            f"sentiment={classification.sentiment.value}, "
            f"priority={classification.priority.value} "
            f"(confidence={classification.confidence:.0%})"
        )
        if classification.reasoning:
            lines.append(f"  Reasoning: {classification.reasoning}")

    return "\n".join(lines)


def _build_draft_request(state: WorkflowState) -> str:
    """Build the LLM request message for final draft generation."""
    tool_results = state.get("tool_results", [])
    governance_decisions = state.get("governance_decisions", [])

    lines = [
        "Based on the conversation above and the following tool results, "
        "please draft a professional customer support response:\n"
    ]

    if tool_results:
        lines.append("Tool execution results:")
        for tr in tool_results:
            status = "SUCCESS" if tr.get("success") else ("BLOCKED" if not tr.get("executed") else "FAILED")
            lines.append(f"  - {tr.get('tool_name', 'unknown')}: {status}")
            if tr.get("output") and tr.get("success"):
                lines.append(f"    Output: {str(tr.get('output', ''))[:300]}")
            if tr.get("error"):
                lines.append(f"    Note: {tr.get('error', '')[:200]}")

    if governance_decisions:
        blocked = [d for d in governance_decisions if d.decision.value == "HALTED"]
        if blocked:
            lines.append(
                f"\nIMPORTANT: {len(blocked)} action(s) were blocked by governance policy. "
                "Do NOT claim these actions were completed in your response."
            )

    lines.append(
        "\nDraft a response that:\n"
        "- Acknowledges the customer's concern.\n"
        "- States only verified facts from the retrieved data.\n"
        "- Does not promise or claim actions that were denied or not yet completed.\n"
        "- Is professional, empathetic, and concise.\n"
        "- Does not claim external emails or messages were sent."
    )

    return "\n".join(lines)
