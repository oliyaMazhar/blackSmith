"""Unit and integration tests for transactional database operations (§23.3).

Tests:
1. Successful refund execution atomically updates refunds, orders, inventory, and events.
2. Injected failure triggers complete rollback leaving zero orphaned modifications.
3. Duplicate execution is strictly blocked by state check and idempotency key.
4. Inventory restocking correctly updates return quantities per policy.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest

from database.connection import get_connection, get_transaction
from database.transactions import (
    DuplicateRefundExecutionError,
    RefundIneligibleError,
    approve_refund_request,
    create_refund_request,
    execute_refund,
)


@pytest.fixture
def temp_order_and_items():
    """Create a temporary clean order with order items for testing transactions."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Pick product #1
            cur.execute("SELECT product_id, price FROM products WHERE product_id = 1;")
            prod_row = cur.fetchone()
            product_id, unit_price = prod_row

            # Insert test order
            cur.execute(
                """
                INSERT INTO orders (customer_id, order_status, payment_status, placed_at)
                VALUES (1, 'delivered', 'paid', CURRENT_TIMESTAMP)
                RETURNING order_id;
                """
            )
            order_id = cur.fetchone()[0]

            # Insert order item
            cur.execute(
                """
                INSERT INTO order_items (order_id, product_id, quantity, unit_price)
                VALUES (%s, %s, 1, %s)
                RETURNING order_item_id;
                """,
                (order_id, product_id, unit_price),
            )
            item_id = cur.fetchone()[0]
        conn.commit()

    yield {
        "order_id": order_id,
        "customer_id": 1,
        "product_id": product_id,
        "unit_price": Decimal(str(unit_price)),
    }

    # Teardown: clean up test order and any linked refunds/events
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM refunds WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM order_items WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM orders WHERE order_id = %s;", (order_id,))
        conn.commit()


def test_successful_refund_execution(temp_order_and_items):
    """Executing an approved refund must update refund, order, inventory, and event atomically."""
    order_id = temp_order_and_items["order_id"]
    customer_id = temp_order_and_items["customer_id"]
    amount = temp_order_and_items["unit_price"]
    product_id = temp_order_and_items["product_id"]

    # Initial inventory state
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT quantity_returned FROM inventory WHERE product_id = %s;", (product_id,))
            initial_returned = cur.fetchone()[0]

    # 1. Create request
    refund_id = create_refund_request(
        order_id=order_id,
        customer_id=customer_id,
        amount=amount,
        reason="Test refund for satisfaction guarantee",
    )
    assert refund_id > 0

    # 2. Approve request
    approved = approve_refund_request(refund_id=refund_id, reviewer="test_governance")
    assert approved is True

    # 3. Execute refund
    key = f"idemp_test_{uuid.uuid4().hex[:12]}"
    result = execute_refund(
        refund_id=refund_id,
        idempotency_key=key,
        reviewer="test_executor",
        restock_inventory=True,
    )

    assert result["status"] == "processed"
    assert result["payment_status"] == "refunded"
    assert result["idempotency_key"] == key

    # 4. Verify database state
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check refund row
            cur.execute("SELECT status, processed_at, idempotency_key FROM refunds WHERE refund_id = %s;", (refund_id,))
            ref_row = cur.fetchone()
            assert ref_row[0] == "processed"
            assert ref_row[1] is not None
            assert ref_row[2] == key

            # Check order row
            cur.execute("SELECT payment_status, order_status FROM orders WHERE order_id = %s;", (order_id,))
            ord_row = cur.fetchone()
            assert ord_row[0] == "refunded"
            assert ord_row[1] == "returned"

            # Check inventory row
            cur.execute("SELECT quantity_returned FROM inventory WHERE product_id = %s;", (product_id,))
            new_returned = cur.fetchone()[0]
            assert new_returned == initial_returned + 1

            # Check events table
            cur.execute("SELECT COUNT(*) FROM events WHERE refund_id = %s AND event_type = 'refund_processed';", (refund_id,))
            event_count = cur.fetchone()[0]
            assert event_count == 1


def test_duplicate_execution_prevented(temp_order_and_items):
    """Calling execute_refund on an already executed refund or re-using a key must raise an error."""
    order_id = temp_order_and_items["order_id"]
    customer_id = temp_order_and_items["customer_id"]
    amount = temp_order_and_items["unit_price"]

    refund_id = create_refund_request(
        order_id=order_id,
        customer_id=customer_id,
        amount=amount,
        reason="Test duplicate execution",
    )
    approve_refund_request(refund_id=refund_id)

    key = f"idemp_test_{uuid.uuid4().hex[:12]}"
    execute_refund(refund_id=refund_id, idempotency_key=key)

    # Re-executing same refund with different key
    with pytest.raises(DuplicateRefundExecutionError) as exc_info:
        execute_refund(refund_id=refund_id, idempotency_key=f"another_{uuid.uuid4().hex[:8]}")
    assert "already been executed" in str(exc_info.value).lower()

    # Re-executing with the exact same idempotency key on another refund
    refund_id2 = create_refund_request(
        order_id=order_id,
        customer_id=customer_id,
        amount=amount,
        reason="Second refund request",
    )
    with pytest.raises(DuplicateRefundExecutionError) as exc_info2:
        execute_refund(refund_id=refund_id2, idempotency_key=key)
    assert "idempotency key" in str(exc_info2.value).lower()


def test_atomic_rollback_on_injected_failure(temp_order_and_items):
    """If an error occurs midway through execution, all changes must roll back completely."""
    order_id = temp_order_and_items["order_id"]
    customer_id = temp_order_and_items["customer_id"]
    amount = temp_order_and_items["unit_price"]
    product_id = temp_order_and_items["product_id"]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT quantity_returned FROM inventory WHERE product_id = %s;", (product_id,))
            initial_returned = cur.fetchone()[0]

    refund_id = create_refund_request(
        order_id=order_id,
        customer_id=customer_id,
        amount=amount,
        reason="Test atomic rollback failure injection",
    )
    approve_refund_request(refund_id=refund_id)

    key = f"idemp_fail_{uuid.uuid4().hex[:12]}"

    # Execute with failure injected before commit
    with pytest.raises(RuntimeError) as exc_info:
        execute_refund(
            refund_id=refund_id,
            idempotency_key=key,
            _inject_failure_before_commit=True,
        )
    assert "simulated transaction failure" in str(exc_info.value).lower()

    # Verify atomic rollback: refund must remain 'approved', not 'processed'
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, idempotency_key FROM refunds WHERE refund_id = %s;", (refund_id,))
            ref_row = cur.fetchone()
            assert ref_row[0] == "approved"
            assert ref_row[1] is None

            # Order status must remain 'paid' / 'delivered'
            cur.execute("SELECT payment_status FROM orders WHERE order_id = %s;", (order_id,))
            ord_row = cur.fetchone()
            assert ord_row[0] == "paid"

            # Inventory must not have changed
            cur.execute("SELECT quantity_returned FROM inventory WHERE product_id = %s;", (product_id,))
            returned_now = cur.fetchone()[0]
            assert returned_now == initial_returned

            # No refund_processed event should exist
            cur.execute("SELECT COUNT(*) FROM events WHERE refund_id = %s AND event_type = 'refund_processed';", (refund_id,))
            assert cur.fetchone()[0] == 0
