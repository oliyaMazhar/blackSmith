"""Order inspection and balance calculation tools backed by PostgreSQL.

Provides:
- order.get: Retrieve order header and total
- order.items: Retrieve line items belonging to an order
- order.status: Retrieve fulfillment and payment status
- order.refundable_balance: Calculate total paid, prior refunds, and remaining refundable balance
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.models import ActionCategory, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry
from database.connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# Argument Schemas
# ============================================================================

class GetOrderInput(BaseModel):
    """Input arguments for reading order details."""
    order_id: int = Field(..., description="Unique Order ID")


class GetOrderItemsInput(BaseModel):
    """Input arguments for reading order line items."""
    order_id: int = Field(..., description="Unique Order ID")


class GetOrderStatusInput(BaseModel):
    """Input arguments for checking order fulfillment status."""
    order_id: int = Field(..., description="Unique Order ID")


class GetOrderRefundableBalanceInput(BaseModel):
    """Input arguments for computing remaining refundable balance."""
    order_id: int = Field(..., description="Unique Order ID")


# ============================================================================
# Tool Implementations
# ============================================================================

def get_order(order_id: int) -> Dict[str, Any]:
    """Retrieve order header details, customer identifier, and total amount."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.campaign_id, o.order_status, o.payment_status,
                       o.payment_fingerprint, o.shipping_address_hash, o.placed_at,
                       COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_amount,
                       COUNT(oi.order_item_id) AS item_count
                FROM orders o
                LEFT JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.order_id = %s
                GROUP BY o.order_id, o.customer_id, o.campaign_id, o.order_status, o.payment_status,
                         o.payment_fingerprint, o.shipping_address_hash, o.placed_at;
                """,
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "order_id": order_id,
                    "message": f"Order #{order_id} does not exist in records.",
                }

            return {
                "found": True,
                "order_id": row[0],
                "customer_id": row[1],
                "campaign_id": row[2],
                "order_status": row[3],
                "payment_status": row[4],
                "payment_fingerprint": row[5],
                "shipping_address_hash": row[6],
                "placed_at": row[7].isoformat() if row[7] else None,
                "total_amount": float(row[8]),
                "item_count": row[9],
            }


def get_order_items(order_id: int) -> Dict[str, Any]:
    """Retrieve line items belonging to an order with product info and unit prices."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check order existence
            cur.execute("SELECT order_id FROM orders WHERE order_id = %s;", (order_id,))
            if not cur.fetchone():
                return {
                    "found": False,
                    "order_id": order_id,
                    "items": [],
                    "message": f"Order #{order_id} does not exist in records.",
                }

            cur.execute(
                """
                SELECT oi.order_item_id, oi.order_id, oi.product_id, p.name AS product_name,
                       p.sku, oi.quantity, oi.unit_price, (oi.quantity * oi.unit_price) AS line_total,
                       p.is_limited_edition
                FROM order_items oi
                JOIN products p ON oi.product_id = p.product_id
                WHERE oi.order_id = %s
                ORDER BY oi.order_item_id ASC;
                """,
                (order_id,),
            )
            rows = cur.fetchall()
            items = [
                {
                    "order_item_id": r[0],
                    "order_id": r[1],
                    "product_id": r[2],
                    "product_name": r[3],
                    "sku": r[4],
                    "quantity": r[5],
                    "unit_price": float(r[6]),
                    "line_total": float(r[7]),
                    "is_limited_edition": bool(r[8]),
                }
                for r in rows
            ]
            return {
                "found": True,
                "order_id": order_id,
                "item_count": len(items),
                "items": items,
            }


def get_order_status(order_id: int) -> Dict[str, Any]:
    """Check current fulfillment and payment lifecycle status of an order."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id, customer_id, order_status, payment_status, placed_at
                FROM orders
                WHERE order_id = %s;
                """,
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "order_id": order_id,
                    "message": f"Order #{order_id} does not exist in records.",
                }

            return {
                "found": True,
                "order_id": row[0],
                "customer_id": row[1],
                "order_status": row[2],
                "payment_status": row[3],
                "placed_at": row[4].isoformat() if row[4] else None,
            }


def get_order_refundable_balance(order_id: int) -> Dict[str, Any]:
    """Calculate total order amount, amount already refunded, and remaining balance."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.payment_status,
                       COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_order_amount
                FROM orders o
                LEFT JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.order_id = %s
                GROUP BY o.order_id, o.customer_id, o.payment_status;
                """,
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "found": False,
                    "order_id": order_id,
                    "message": f"Order #{order_id} does not exist in records.",
                }

            _, customer_id, payment_status, total_amount_raw = row
            total_order_amount = Decimal(str(total_amount_raw))

            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0.00)
                FROM refunds
                WHERE order_id = %s AND status IN ('approved', 'processed', 'executed');
                """,
                (order_id,),
            )
            total_refunded = Decimal(str(cur.fetchone()[0]))
            remaining_balance = max(Decimal("0.00"), total_order_amount - total_refunded)

            return {
                "found": True,
                "order_id": order_id,
                "customer_id": customer_id,
                "payment_status": payment_status,
                "total_order_amount": float(total_order_amount),
                "total_refunded": float(total_refunded),
                "remaining_refundable_balance": float(remaining_balance),
                "is_fully_refunded": remaining_balance <= Decimal("0.00"),
            }


# ============================================================================
# Tool Registration
# ============================================================================

def register_order_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all order domain tools into the target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="order.get",
            description="Retrieve order header details, customer identifier, and total amount by order ID.",
            arg_schema=GetOrderInput,
            function=get_order,
            permissions=["orders.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="order.items",
            description="Retrieve line items belonging to an order with quantities and prices.",
            arg_schema=GetOrderItemsInput,
            function=get_order_items,
            permissions=["orders.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="order.status",
            description="Check current fulfillment and payment lifecycle status of an order.",
            arg_schema=GetOrderStatusInput,
            function=get_order_status,
            permissions=["orders.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="order.refundable_balance",
            description="Calculate total order price, prior refunds, and remaining allowable refundable balance.",
            arg_schema=GetOrderRefundableBalanceInput,
            function=get_order_refundable_balance,
            permissions=["orders.read", "refunds.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )
