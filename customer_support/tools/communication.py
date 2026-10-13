"""Communication and response drafting tools backed by PostgreSQL.

Provides:
- communication.get_messages: Retrieve customer and ticket communication history
- ticket.save_draft: Persist drafted customer response to support_tickets record
  (Explicit boundary: saving a draft is NOT sending an email or message)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from core.models import ActionCategory, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry
from database.connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# Argument Schemas
# ============================================================================

class GetMessagesInput(BaseModel):
    """Input arguments for reading customer message history."""
    ticket_id: Optional[int] = Field(None, description="Support ticket ID")
    customer_id: Optional[int] = Field(None, description="Customer ID")
    limit: int = Field(20, ge=1, le=100, description="Max messages to retrieve")

    @model_validator(mode="after")
    def validate_identifier(self) -> GetMessagesInput:
        if self.ticket_id is None and self.customer_id is None:
            raise ValueError("Either ticket_id or customer_id must be provided to retrieve messages.")
        return self


class SaveDraftInput(BaseModel):
    """Input arguments for saving a draft response on a support ticket."""
    ticket_id: int = Field(..., description="Ticket ID to attach draft response to")
    response_draft: str = Field(..., min_length=5, description="Draft response body to be reviewed or saved")


# ============================================================================
# Tool Implementations
# ============================================================================

def get_messages(
    ticket_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Retrieve message history for a support ticket or customer."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_id, customer_id, channel, direction, subject, body, ticket_id, created_at
                FROM messages
                WHERE (%s IS NULL OR ticket_id = %s)
                  AND (%s IS NULL OR customer_id = %s)
                ORDER BY created_at ASC
                LIMIT %s;
                """,
                (ticket_id, ticket_id, customer_id, customer_id, limit),
            )
            rows = cur.fetchall()
            messages = [
                {
                    "message_id": r[0],
                    "customer_id": r[1],
                    "channel": r[2],
                    "direction": r[3],
                    "subject": r[4],
                    "body": r[5],
                    "ticket_id": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]
            return {
                "found": len(messages) > 0,
                "count": len(messages),
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "messages": messages,
            }


def save_response_draft(
    ticket_id: int,
    response_draft: str,
) -> Dict[str, Any]:
    """Save a drafted response message on a support ticket in the database.
    
    Note: This persists the draft locally for review and records. It does not
    dispatch live external email, SMS, or WhatsApp communications.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE support_tickets
                SET response_draft = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE ticket_id = %s
                RETURNING ticket_id, customer_id, order_id, category, priority, workflow_status, updated_at;
                """,
                (response_draft, ticket_id),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "saved": False,
                    "ticket_id": ticket_id,
                    "message": f"Support ticket #{ticket_id} does not exist in records.",
                }

            return {
                "saved": True,
                "ticket_id": row[0],
                "customer_id": row[1],
                "order_id": row[2],
                "category": row[3],
                "priority": row[4],
                "workflow_status": row[5],
                "response_draft": response_draft,
                "updated_at": row[6].isoformat() if row[6] else None,
                "message": (
                    f"Response draft successfully saved to ticket #{ticket_id}. "
                    f"Draft is stored internally and not dispatched to external mail servers."
                ),
            }


# ============================================================================
# Tool Registration
# ============================================================================

def register_communication_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all communication and drafting tools into target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="communication.get_messages",
            description="Retrieve communication message history for a support ticket or customer account.",
            arg_schema=GetMessagesInput,
            function=get_messages,
            permissions=["messages.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.COMMUNICATION,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="ticket.save_draft",
            description="Save a drafted response message on a support ticket record. Does NOT dispatch external communication.",
            arg_schema=SaveDraftInput,
            function=save_response_draft,
            permissions=["tickets.write"],
            risk_level=RiskLevel.LOW,
            is_mutation=True,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.COMMUNICATION,
        )
    )
