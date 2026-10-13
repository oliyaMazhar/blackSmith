"""Refund workflow tools backed by PostgreSQL and ACID transaction boundaries.

Strictly enforces separation of responsibilities:
- refund.history: Read refund records
- refund.check_eligibility: Evaluate business return policy without mutating state
- refund.calculate_amount: Compute allowable refund limit
- refund.create_request: Create a refund in 'requested' state
- refund.approve_request: Approve a requested refund
- refund.reject_request: Reject a requested or approved refund with recorded reason
- refund.execute: Atomically execute an approved refund in PostgreSQL
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.eligibility import check_refund_eligibility
from core.models import ActionCategory, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry
from database.connection import get_connection
from database.transactions import (
    approve_refund_request,
    create_refund_request,
    execute_refund,
    reject_refund_request,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Argument Schemas
# ============================================================================

class GetRefundHistoryInput(BaseModel):
    """Input arguments for querying refund records."""
    order_id: Optional[int] = Field(None, description="Filter refunds by order ID")
    customer_id: Optional[int] = Field(None, description="Filter refunds by customer ID")
    refund_id: Optional[int] = Field(None, description="Filter by exact refund ID")
    limit: int = Field(20, ge=1, le=100, description="Max refund records to return")


class CheckRefundEligibilityInput(BaseModel):
    """Input arguments for checking return eligibility without mutation."""
    order_id: int = Field(..., description="Order ID to evaluate against return policies")
    requested_amount: Optional[Decimal] = Field(None, description="Proposed refund amount")
    is_defective: bool = Field(False, description="Whether claim involves defective hardware (90-day window)")


class CalculateRefundAmountInput(BaseModel):
    """Input arguments for computing maximum allowable refund amount."""
    order_id: int = Field(..., description="Order ID to inspect")


class CreateRefundRequestInput(BaseModel):
    """Input arguments for submitting a new refund request."""
    order_id: int = Field(..., description="Order ID")
    customer_id: int = Field(..., description="Customer ID")
    amount: Decimal = Field(..., gt=Decimal("0.00"), description="Requested refund amount in USD")
    reason: str = Field(..., min_length=3, description="Customer-provided reason for refund")


class ApproveRefundRequestInput(BaseModel):
    """Input arguments for approving a refund request."""
    refund_id: int = Field(..., description="Refund record ID to approve")
    reviewer: str = Field("governance_agent", description="Supervisor or system approving request")


class RejectRefundRequestInput(BaseModel):
    """Input arguments for rejecting a refund request."""
    refund_id: int = Field(..., description="Refund record ID to reject")
    reason: str = Field(..., min_length=3, description="Documented reason for rejection")
    reviewer: str = Field("governance_agent", description="Supervisor or system rejecting request")


class ExecuteRefundInput(BaseModel):
    """Input arguments for executing an approved refund in PostgreSQL."""
    refund_id: int = Field(..., description="Approved refund record ID to execute")
    idempotency_key: str = Field(..., min_length=8, description="Unique idempotency token preventing duplicate payouts")
    reviewer: str = Field("governance_executor", description="Executing system entity")
    restock_inventory: bool = Field(True, description="Whether items returned should be restocked in warehouse")


# ============================================================================
# Tool Implementations
# ============================================================================

def get_refund_history(
    order_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    refund_id: Optional[int] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Retrieve historical refund records and lifecycle states."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT refund_id, order_id, customer_id, amount, reason, status,
                       requested_at, reviewed_at, processed_at, reviewed_by, idempotency_key
                FROM refunds
                WHERE (%s IS NULL OR order_id = %s)
                  AND (%s IS NULL OR customer_id = %s)
                  AND (%s IS NULL OR refund_id = %s)
                ORDER BY requested_at DESC
                LIMIT %s;
                """,
                (order_id, order_id, customer_id, customer_id, refund_id, refund_id, limit),
            )
            rows = cur.fetchall()
            refunds = [
                {
                    "refund_id": r[0],
                    "order_id": r[1],
                    "customer_id": r[2],
                    "amount": float(r[3]),
                    "reason": r[4],
                    "status": r[5],
                    "requested_at": r[6].isoformat() if r[6] else None,
                    "reviewed_at": r[7].isoformat() if r[7] else None,
                    "processed_at": r[8].isoformat() if r[8] else None,
                    "reviewed_by": r[9],
                    "idempotency_key": r[10],
                }
                for r in rows
            ]
            return {
                "found": len(refunds) > 0,
                "count": len(refunds),
                "refunds": refunds,
            }


def check_refund_eligibility_tool(
    order_id: int,
    requested_amount: Optional[Decimal] = None,
    is_defective: bool = False,
) -> Dict[str, Any]:
    """Evaluate order against business return rules without modifying any records."""
    eligibility = check_refund_eligibility(
        order_id=order_id,
        requested_amount=requested_amount,
        is_defective=is_defective,
    )
    return {
        "is_eligible": eligibility.is_eligible,
        "order_id": eligibility.order_id,
        "max_refundable_amount": float(eligibility.max_refundable_amount),
        "reason": eligibility.reason,
        "violations": eligibility.violations,
        "policy_checks": [
            {
                "rule_name": c.rule_name,
                "passed": c.passed,
                "message": c.message,
            }
            for c in eligibility.policy_checks
        ],
    }


def calculate_refund_amount(order_id: int) -> Dict[str, Any]:
    """Calculate allowable refund amount based on order items and previous refunds."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.payment_status,
                       COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_amount
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

            _, customer_id, payment_status, total_raw = row
            total_order = Decimal(str(total_raw))

            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0.00)
                FROM refunds
                WHERE order_id = %s AND status IN ('approved', 'processed', 'executed');
                """,
                (order_id,),
            )
            total_refunded = Decimal(str(cur.fetchone()[0]))
            max_allowable = max(Decimal("0.00"), total_order - total_refunded)

            return {
                "found": True,
                "order_id": order_id,
                "customer_id": customer_id,
                "payment_status": payment_status,
                "total_order_amount": float(total_order),
                "already_refunded": float(total_refunded),
                "max_allowable_refund": float(max_allowable),
            }


def create_refund_request_tool(
    order_id: int,
    customer_id: int,
    amount: Decimal,
    reason: str,
) -> Dict[str, Any]:
    """Create a new refund request record with status 'requested'."""
    refund_id = create_refund_request(
        order_id=order_id,
        customer_id=customer_id,
        amount=amount,
        reason=reason,
    )
    return {
        "success": True,
        "refund_id": refund_id,
        "status": "requested",
        "order_id": order_id,
        "customer_id": customer_id,
        "amount": float(amount),
        "message": f"Refund request #{refund_id} successfully created with status 'requested'. Requires approval before execution.",
    }


def approve_refund_request_tool(
    refund_id: int,
    reviewer: str = "governance_agent",
) -> Dict[str, Any]:
    """Transition a refund request from 'requested' to 'approved'."""
    success = approve_refund_request(refund_id=refund_id, reviewer=reviewer)
    return {
        "success": success,
        "refund_id": refund_id,
        "status": "approved",
        "reviewer": reviewer,
        "message": f"Refund #{refund_id} approved by '{reviewer}'. Ready for authorized transactional execution.",
    }


def reject_refund_request_tool(
    refund_id: int,
    reason: str,
    reviewer: str = "governance_agent",
) -> Dict[str, Any]:
    """Reject a refund request with documented justification."""
    success = reject_refund_request(refund_id=refund_id, reason=reason, reviewer=reviewer)
    return {
        "success": success,
        "refund_id": refund_id,
        "status": "rejected",
        "reason": reason,
        "reviewer": reviewer,
        "message": f"Refund #{refund_id} rejected by '{reviewer}'. Reason: {reason}",
    }


def execute_refund_tool(
    refund_id: int,
    idempotency_key: str,
    reviewer: str = "governance_executor",
    restock_inventory: bool = True,
) -> Dict[str, Any]:
    """Atomically execute an approved refund in PostgreSQL."""
    result = execute_refund(
        refund_id=refund_id,
        idempotency_key=idempotency_key,
        reviewer=reviewer,
        restock_inventory=restock_inventory,
    )
    return {
        "success": True,
        "refund_id": result["refund_id"],
        "order_id": result["order_id"],
        "customer_id": result["customer_id"],
        "amount": result["amount"],
        "status": result["status"],
        "payment_status": result["payment_status"],
        "idempotency_key": result["idempotency_key"],
        "message": f"Refund #{refund_id} executed successfully with status '{result['status']}'. Order payment status updated to '{result['payment_status']}'.",
    }


# ============================================================================
# Tool Registration
# ============================================================================

def register_refund_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all refund domain tools into target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="refund.history",
            description="Retrieve historical refund requests, amounts, and statuses for orders or customers.",
            arg_schema=GetRefundHistoryInput,
            function=get_refund_history,
            permissions=["refunds.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.check_eligibility",
            description="Evaluate order against business return rules (payment status, return window, defect policy, refundable balance) without mutating records.",
            arg_schema=CheckRefundEligibilityInput,
            function=check_refund_eligibility_tool,
            permissions=["refunds.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.calculate_amount",
            description="Calculate permissible refund amount based on order items and previous refunds.",
            arg_schema=CalculateRefundAmountInput,
            function=calculate_refund_amount,
            permissions=["refunds.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.create_request",
            description="Create a formal refund request in 'requested' status. Does NOT approve or execute payout.",
            arg_schema=CreateRefundRequestInput,
            function=create_refund_request_tool,
            permissions=["refunds.create"],
            risk_level=RiskLevel.MEDIUM,
            is_mutation=True,
            side_effect=True,
            reversible=False,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.approve_request",
            description="Approve a previously requested refund. Transitions status from 'requested' to 'approved'. Does NOT execute payout.",
            arg_schema=ApproveRefundRequestInput,
            function=approve_refund_request_tool,
            permissions=["refunds.approve"],
            risk_level=RiskLevel.HIGH,
            is_mutation=True,
            side_effect=True,
            reversible=False,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.reject_request",
            description="Reject a refund request with a documented justification. Transitions status to 'rejected'.",
            arg_schema=RejectRefundRequestInput,
            function=reject_refund_request_tool,
            permissions=["refunds.reject"],
            risk_level=RiskLevel.MEDIUM,
            is_mutation=True,
            side_effect=True,
            reversible=False,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="refund.execute",
            description="Atomically execute an approved refund in PostgreSQL: updates status to 'processed', adjusts order status, restocks inventory, and writes audit event.",
            arg_schema=ExecuteRefundInput,
            function=execute_refund_tool,
            permissions=["refunds.execute"],
            risk_level=RiskLevel.CRITICAL,
            is_mutation=True,
            side_effect=True,
            reversible=False,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )
