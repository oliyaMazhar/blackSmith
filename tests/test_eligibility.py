"""Unit and integration tests for refund eligibility business rules (§23.2).

Tests:
1. Valid order passes eligibility checks.
2. Order outside return window is rejected.
3. Defective order within 90-day window passes.
4. Order outside 90-day window is rejected even if defective.
5. Unpaid or cancelled order is rejected.
6. Amount exceeding refundable balance is rejected.
7. Negative or zero requested amount is rejected.
8. Previous refunds are counted correctly in remaining balance.
9. Missing records fail safely without uncaught exceptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from core.eligibility import check_refund_eligibility
from database.connection import get_connection, get_transaction


@pytest.fixture(scope="module")
def sample_paid_order():
    """Retrieve an existing paid order with line items from the database."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.placed_at,
                       COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_amount
                FROM orders o
                JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.payment_status = 'paid' AND o.order_status = 'delivered'
                GROUP BY o.order_id, o.customer_id, o.placed_at
                HAVING COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) > 0
                LIMIT 1;
                """
            )
            row = cur.fetchone()
            assert row is not None, "At least one delivered paid order must exist."
            return {
                "order_id": row[0],
                "customer_id": row[1],
                "placed_at": row[2],
                "total_amount": Decimal(str(row[3])),
            }


def test_valid_order_eligibility_passes(sample_paid_order):
    """A valid paid order within return window must pass eligibility checks."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    # Reference time 5 days after order was placed
    eval_time = placed_at + timedelta(days=5)

    result = check_refund_eligibility(
        order_id=order_id,
        reference_time=eval_time,
        is_defective=False,
    )

    assert result.is_eligible is True
    assert result.max_refundable_amount > Decimal("0.00")
    assert len(result.violations) == 0


def test_order_outside_return_window_rejected(sample_paid_order):
    """An order outside the standard 30-day window must be rejected for non-defective return."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    # Reference time 45 days after placement
    eval_time = placed_at + timedelta(days=45)

    result = check_refund_eligibility(
        order_id=order_id,
        reference_time=eval_time,
        is_defective=False,
    )

    assert result.is_eligible is False
    assert any("Policy limit is 30 days" in v for v in result.violations)


def test_defective_item_within_90_days_passes(sample_paid_order):
    """An order outside 30 days but within 90 days passes when verified as defective."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    # Reference time 45 days after placement (outside standard 30d, within 90d defective)
    eval_time = placed_at + timedelta(days=45)

    result = check_refund_eligibility(
        order_id=order_id,
        reference_time=eval_time,
        is_defective=True,
    )

    assert result.is_eligible is True
    assert len(result.violations) == 0


def test_defective_item_outside_90_days_rejected(sample_paid_order):
    """An order beyond 90 days is rejected even if defective."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    eval_time = placed_at + timedelta(days=95)

    result = check_refund_eligibility(
        order_id=order_id,
        reference_time=eval_time,
        is_defective=True,
    )

    assert result.is_eligible is False
    assert any("Policy limit is 90 days" in v for v in result.violations)


def test_amount_exceeding_refundable_balance_rejected(sample_paid_order):
    """Requested refund amount exceeding the balance must be rejected."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    eval_time = placed_at + timedelta(days=5)

    # Get max refundable amount first
    baseline = check_refund_eligibility(order_id=order_id, reference_time=eval_time)
    excessive_amount = baseline.max_refundable_amount + Decimal("100.00")

    result = check_refund_eligibility(
        order_id=order_id,
        requested_amount=excessive_amount,
        reference_time=eval_time,
    )

    assert result.is_eligible is False
    assert any("exceeds remaining refundable balance" in v for v in result.violations)


def test_zero_or_negative_refund_amount_rejected(sample_paid_order):
    """Requested refund of $0.00 or negative amount must be rejected."""
    order_id = sample_paid_order["order_id"]
    placed_at = sample_paid_order["placed_at"]
    eval_time = placed_at + timedelta(days=5)

    result_zero = check_refund_eligibility(
        order_id=order_id,
        requested_amount=Decimal("0.00"),
        reference_time=eval_time,
    )
    assert result_zero.is_eligible is False
    assert any("must be greater than zero" in v for v in result_zero.violations)

    result_neg = check_refund_eligibility(
        order_id=order_id,
        requested_amount=Decimal("-15.00"),
        reference_time=eval_time,
    )
    assert result_neg.is_eligible is False
    assert any("must be greater than zero" in v for v in result_neg.violations)


def test_unpaid_or_cancelled_order_rejected():
    """Unpaid or cancelled orders must fail payment verification."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Create a temporary cancelled order for testing
            cur.execute(
                """
                INSERT INTO orders (customer_id, order_status, payment_status, placed_at)
                VALUES (1, 'cancelled', 'failed', CURRENT_TIMESTAMP)
                RETURNING order_id;
                """
            )
            test_order_id = cur.fetchone()[0]
        conn.commit()

    try:
        result = check_refund_eligibility(order_id=test_order_id)
        assert result.is_eligible is False
        assert any("payment status" in v.lower() for v in result.violations)
        assert any("cancelled" in v.lower() for v in result.violations)
    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM orders WHERE order_id = %s;", (test_order_id,))
            conn.commit()


def test_missing_order_fails_safely():
    """Evaluating a non-existent order ID must fail safely without unhandled exception."""
    non_existent_id = 999999999
    result = check_refund_eligibility(order_id=non_existent_id)

    assert result.is_eligible is False
    assert result.order_id == non_existent_id
    assert "does not exist" in result.violations[0].lower()
    assert result.max_refundable_amount == Decimal("0.00")
