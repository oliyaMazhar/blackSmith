"""Customer management tools backed by PostgreSQL.

Provides:
- customer.get: Retrieve customer profile by ID or email
- customer.orders: Retrieve customer order history
- customer.activity: Retrieve web sessions, tickets, messages, and events
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

class GetCustomerInput(BaseModel):
    """Input arguments for reading a customer profile."""
    customer_id: Optional[int] = Field(None, description="Unique database ID of customer")
    email: Optional[str] = Field(None, description="Email address of customer")

    @model_validator(mode="after")
    def validate_identifier(self) -> GetCustomerInput:
        if self.customer_id is None and (not self.email or not self.email.strip()):
            raise ValueError("Either customer_id or email must be provided to look up a customer.")
        return self


class GetCustomerOrdersInput(BaseModel):
    """Input arguments for reading customer order history."""
    customer_id: int = Field(..., description="Customer ID whose orders should be retrieved")
    limit: int = Field(10, ge=1, le=50, description="Maximum number of recent orders to return")


class GetCustomerActivityInput(BaseModel):
    """Input arguments for inspecting recent customer activity."""
    customer_id: int = Field(..., description="Customer ID whose activity to retrieve")
    limit: int = Field(15, ge=1, le=50, description="Maximum records per activity stream to return")


# ============================================================================
# Tool Implementations
# ============================================================================

def get_customer(
    customer_id: Optional[int] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve customer account profile and standing."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_id, email, full_name, account_status, identity_verified, created_at
                FROM customers
                WHERE (%s IS NOT NULL AND customer_id = %s)
                   OR (%s IS NOT NULL AND LOWER(email) = LOWER(%s))
                LIMIT 1;
                """,
                (customer_id, customer_id, email, email),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "customer_id": customer_id,
                    "email": email,
                    "message": f"No customer found with identifier (customer_id={customer_id}, email={email}).",
                }

            return {
                "found": True,
                "customer_id": row[0],
                "email": row[1],
                "full_name": row[2],
                "account_status": row[3],
                "identity_verified": bool(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
            }


def get_customer_orders(customer_id: int, limit: int = 10) -> Dict[str, Any]:
    """Retrieve customer's order history with totals and item counts."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check customer existence
            cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s;", (customer_id,))
            if not cur.fetchone():
                return {
                    "found": False,
                    "customer_id": customer_id,
                    "orders": [],
                    "message": f"Customer #{customer_id} does not exist in records.",
                }

            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.campaign_id, o.order_status, o.payment_status,
                       o.placed_at, COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_amount,
                       COUNT(oi.order_item_id) AS item_count
                FROM orders o
                LEFT JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.customer_id = %s
                GROUP BY o.order_id, o.customer_id, o.campaign_id, o.order_status, o.payment_status, o.placed_at
                ORDER BY o.placed_at DESC
                LIMIT %s;
                """,
                (customer_id, limit),
            )
            rows = cur.fetchall()
            orders = [
                {
                    "order_id": r[0],
                    "customer_id": r[1],
                    "campaign_id": r[2],
                    "order_status": r[3],
                    "payment_status": r[4],
                    "placed_at": r[5].isoformat() if r[5] else None,
                    "total_amount": float(r[6]),
                    "item_count": r[7],
                }
                for r in rows
            ]
            return {
                "found": True,
                "customer_id": customer_id,
                "order_count": len(orders),
                "orders": orders,
            }


def get_customer_activity(customer_id: int, limit: int = 15) -> Dict[str, Any]:
    """Retrieve recent customer sessions, tickets, messages, and events."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s;", (customer_id,))
            if not cur.fetchone():
                return {
                    "found": False,
                    "customer_id": customer_id,
                    "message": f"Customer #{customer_id} does not exist in records.",
                }

            # 1. Sessions
            cur.execute(
                """
                SELECT session_id, ip_address::text, device_fingerprint, created_at
                FROM customer_sessions
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (customer_id, limit),
            )
            sessions = [
                {
                    "session_id": r[0],
                    "ip_address": r[1],
                    "device_fingerprint": r[2],
                    "created_at": r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]

            # 2. Tickets
            cur.execute(
                """
                SELECT ticket_id, category, sentiment, priority, workflow_status, created_at
                FROM support_tickets
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (customer_id, limit),
            )
            tickets = [
                {
                    "ticket_id": r[0],
                    "category": r[1],
                    "sentiment": r[2],
                    "priority": r[3],
                    "workflow_status": r[4],
                    "created_at": r[5].isoformat() if r[5] else None,
                }
                for r in cur.fetchall()
            ]

            # 3. Messages
            cur.execute(
                """
                SELECT message_id, channel, direction, subject, created_at
                FROM messages
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (customer_id, limit),
            )
            msgs = [
                {
                    "message_id": r[0],
                    "channel": r[1],
                    "direction": r[2],
                    "subject": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in cur.fetchall()
            ]

            # 4. Events
            cur.execute(
                """
                SELECT event_id, event_type, order_id, refund_id, occurred_at
                FROM events
                WHERE customer_id = %s
                ORDER BY occurred_at DESC
                LIMIT %s;
                """,
                (customer_id, limit),
            )
            evts = [
                {
                    "event_id": r[0],
                    "event_type": r[1],
                    "order_id": r[2],
                    "refund_id": r[3],
                    "occurred_at": r[4].isoformat() if r[4] else None,
                }
                for r in cur.fetchall()
            ]

            return {
                "found": True,
                "customer_id": customer_id,
                "sessions": sessions,
                "tickets": tickets,
                "messages": msgs,
                "events": evts,
            }


# ============================================================================
# Tool Registration
# ============================================================================

def register_customer_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all customer domain tools into the target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="customer.get",
            description="Retrieve customer account profile and standing by customer ID or email.",
            arg_schema=GetCustomerInput,
            function=get_customer,
            permissions=["customers.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="customer.orders",
            description="Retrieve order history for a customer including status and totals.",
            arg_schema=GetCustomerOrdersInput,
            function=get_customer_orders,
            permissions=["customers.read", "orders.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="customer.activity",
            description="Retrieve recent activity for a customer including web sessions, tickets, messages, and events.",
            arg_schema=GetCustomerActivityInput,
            function=get_customer_activity,
            permissions=["customers.read", "activity.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )
