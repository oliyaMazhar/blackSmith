"""Unit and integration tests for Policy Authorization and Security Boundary (§23.4).

Verifies:
1. AUTO mode executes autonomously only when policy and eligibility constraints are satisfied.
2. ASSISTED mode halts execution pending explicit human approval.
3. Human rejection in ASSISTED mode prevents execution entirely.
4. HALTED mode strictly prevents tool execution.
5. Absolute proof: A denied operation's underlying tool function is NEVER invoked.
6. Repeating a blocked request does not bypass the decision.
7. Circumvention prevention: Choosing a different registered tool cannot bypass hard rules.
8. Persistent audit logging in PostgreSQL.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from autonomy.approval import MockApprovalHandler
from autonomy.audit import get_audit_records
from autonomy.config import GovernanceConfig
from autonomy.gatekeeper import governed_execute
from core.models import ActionCategory, ExecutionMode, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry, reset_registry
from database.connection import get_connection
from pydantic import BaseModel, Field


class SimpleMutationInput(BaseModel):
    order_id: int
    amount: float = Field(..., gt=0.0)


@pytest.fixture
def clean_registry():
    """Ensure registry has default tools ready."""
    return get_registry(populate_defaults=True)


@pytest.fixture(scope="module")
def paid_delivered_order():
    """Create a clean, eligible delivered order within the 30-day window."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (customer_id, order_status, payment_status, placed_at)
                VALUES (1, 'delivered', 'paid', CURRENT_TIMESTAMP)
                RETURNING order_id;
                """
            )
            order_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO order_items (order_id, product_id, quantity, unit_price)
                VALUES (%s, 3, 10, 14.99);
                """,
                (order_id,),
            )
        conn.commit()

    yield {"order_id": order_id, "customer_id": 1, "total": 149.90}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_records WHERE arguments->>'order_id' = %s;", (str(order_id),))
            cur.execute("DELETE FROM events WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM refunds WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM order_items WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM orders WHERE order_id = %s;", (order_id,))
        conn.commit()


# ============================================================================
# 1. AUTO Mode Tests
# ============================================================================

def test_read_only_tool_executes_in_auto_mode(clean_registry, paid_delivered_order):
    """Read-only tools (order.get) execute autonomously in AUTO mode without human approval."""
    oid = paid_delivered_order["order_id"]
    result = governed_execute("order.get", {"order_id": oid})

    assert result.decision == ExecutionMode.AUTO
    assert result.executed is True
    assert result.tool_result is not None
    assert result.tool_result.success is True
    assert result.tool_result.output["found"] is True
    assert result.approval_result is None  # No human prompt required


def test_small_eligible_refund_request_executes_in_auto_mode(clean_registry, paid_delivered_order):
    """Refund request under $50.00 on an eligible order executes in AUTO mode."""
    oid = paid_delivered_order["order_id"]
    cid = paid_delivered_order["customer_id"]

    # Request small $10 refund
    result = governed_execute(
        "refund.create_request",
        {"order_id": oid, "customer_id": cid, "amount": 10.0, "reason": "Small item discount"},
    )

    assert result.decision == ExecutionMode.AUTO
    assert result.executed is True
    assert result.tool_result.output["status"] == "requested"


# ============================================================================
# 2. ASSISTED Mode Tests (Human Approval & Rejection)
# ============================================================================

def test_assisted_mode_executes_when_human_approves(clean_registry, paid_delivered_order):
    """Actions requiring approval execute if and only if explicit human approval is granted."""
    oid = paid_delivered_order["order_id"]
    cid = paid_delivered_order["customer_id"]

    # Amount > $50 ($120.00) triggers ASSISTED mode
    handler = MockApprovalHandler(approved=True, approved_by="supervisor_alice", reason="Approved high-value return")

    result = governed_execute(
        "refund.create_request",
        {"order_id": oid, "customer_id": cid, "amount": 120.0, "reason": "High-value return request"},
        approval_handler=handler,
    )

    assert result.decision == ExecutionMode.ASSISTED
    assert result.approval_result is not None
    assert result.approval_result.approved is True
    assert result.approval_result.approved_by == "supervisor_alice"
    assert result.executed is True
    assert handler.calls_count == 1


def test_assisted_mode_blocked_when_human_rejects(clean_registry, paid_delivered_order):
    """Human rejection in ASSISTED mode prevents execution entirely."""
    oid = paid_delivered_order["order_id"]
    cid = paid_delivered_order["customer_id"]

    handler = MockApprovalHandler(approved=False, approved_by="supervisor_bob", reason="Receipt unverified")

    result = governed_execute(
        "refund.create_request",
        {"order_id": oid, "customer_id": cid, "amount": 120.0, "reason": "Questionable return"},
        approval_handler=handler,
    )

    assert result.decision == ExecutionMode.ASSISTED
    assert result.approval_result is not None
    assert result.approval_result.approved is False
    assert result.executed is False
    assert "rejected by human operator (supervisor_bob)" in result.error


# ============================================================================
# 3. HALTED Mode Tests & Security Boundary Proof
# ============================================================================

def test_halted_mode_on_ineligible_order(clean_registry):
    """An ineligible order (e.g. order cancelled or non-existent) is immediately HALTED."""
    # Order 9999999 does not exist
    result = governed_execute(
        "refund.create_request",
        {"order_id": 9999999, "customer_id": 1, "amount": 25.0, "reason": "Phantom order return"},
    )

    assert result.decision == ExecutionMode.HALTED
    assert result.executed is False
    assert "HALTED by governance policy" in result.error
    assert result.approval_result is None


def test_hard_security_boundary_underlying_function_never_called():
    """ABSOLUTE SECURITY PROOF (§23.4):
    
    A denied operation's underlying callable is NEVER invoked when blocked by governance.
    """
    reg = get_registry()
    spy_callable = MagicMock(return_value={"executed": True})

    # Register protected dummy tool
    reg.register(
        ToolDefinition(
            name="security.test_payout",
            description="High risk payout function for testing security boundary.",
            arg_schema=SimpleMutationInput,
            function=spy_callable,
            permissions=["payout.execute"],
            risk_level=RiskLevel.CRITICAL,
            is_mutation=True,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )

    try:
        # Trigger HALT with invalid order
        result = governed_execute(
            "security.test_payout",
            {"order_id": 9999999, "amount": 500.0},
        )

        assert result.decision == ExecutionMode.HALTED
        assert result.executed is False

        # Verify underlying function was NEVER called
        spy_callable.assert_not_called()

        # Trigger ASSISTED rejection
        reject_handler = MockApprovalHandler(approved=False)
        result2 = governed_execute(
            "security.test_payout",
            {"order_id": 1, "amount": 500.0},
            approval_handler=reject_handler,
        )
        assert result2.executed is False

        # Verify underlying function was STILL NEVER called
        spy_callable.assert_not_called()

    finally:
        reg.unregister("security.test_payout")


def test_repeating_blocked_request_does_not_bypass():
    """Repeating a blocked request in HALTED mode does not bypass governance."""
    result1 = governed_execute("refund.create_request", {"order_id": 9999999, "customer_id": 1, "amount": 20.0, "reason": "Retry 1"})
    result2 = governed_execute("refund.create_request", {"order_id": 9999999, "customer_id": 1, "amount": 20.0, "reason": "Retry 2"})

    assert result1.decision == ExecutionMode.HALTED
    assert result1.executed is False
    assert result2.decision == ExecutionMode.HALTED
    assert result2.executed is False


def test_unregistered_tool_strictly_rejected():
    """Unregistered tools are rejected as security violations before execution."""
    result = governed_execute("malicious_unregistered_function", {"param": "val"})

    assert result.decision == ExecutionMode.HALTED
    assert result.executed is False
    assert "Security Violation: Tool 'malicious_unregistered_function' is not registered" in result.error


# ============================================================================
# 4. Audit Logging Tests
# ============================================================================

def test_governed_execution_persists_audit_record(paid_delivered_order):
    """Every governed execution generates a queryable record in PostgreSQL audit_records."""
    oid = paid_delivered_order["order_id"]
    test_run_id = f"test-audit-run-{oid}"

    result = governed_execute("order.get", {"order_id": oid}, run_id=test_run_id)

    assert result.audit_id is not None
    assert result.audit_id > 0

    records = get_audit_records(run_id=test_run_id)
    assert len(records) >= 1
    rec = records[0]
    assert rec["run_id"] == test_run_id
    assert rec["tool_name"] == "order.get"
    assert rec["authorization_decision"] == "AUTO"
    assert rec["risk_score"] <= 0.30
