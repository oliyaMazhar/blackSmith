"""Transactional database operations for BlackSmith refunds and inventory.

Guarantees atomic multi-table updates, rollback integrity on error,
and strict duplicate execution prevention.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.eligibility import check_refund_eligibility
from database.connection import get_connection, get_transaction


class RefundTransactionError(Exception):
    """Base error for refund transactional failures."""
    pass


class DuplicateRefundExecutionError(RefundTransactionError):
    """Raised when an execution is attempted on an already processed refund or duplicate idempotency key."""
    pass


class RefundIneligibleError(RefundTransactionError):
    """Raised when a refund execution violates eligibility constraints."""
    pass


def create_refund_request(
    order_id: int,
    customer_id: int,
    amount: Decimal | float | str,
    reason: str,
    conn=None,
) -> int:
    """Create a new refund request record with status 'requested'."""
    amount_dec = Decimal(str(amount))
    if amount_dec <= Decimal("0.00"):
        raise ValueError("Refund amount must be greater than zero.")

    def _execute(connection) -> int:
        with get_transaction(connection) as cur:
            # Verify order ownership
            cur.execute(
                "SELECT customer_id FROM orders WHERE order_id = %s;",
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Order #{order_id} does not exist.")
            if row[0] != customer_id:
                raise ValueError(f"Order #{order_id} does not belong to customer #{customer_id}.")

            cur.execute(
                """
                INSERT INTO refunds (order_id, customer_id, amount, reason, status, requested_at)
                VALUES (%s, %s, %s, %s, 'requested', CURRENT_TIMESTAMP)
                RETURNING refund_id;
                """,
                (order_id, customer_id, amount_dec, reason),
            )
            refund_id = cur.fetchone()[0]

            # Record event
            cur.execute(
                """
                INSERT INTO events (event_type, customer_id, order_id, refund_id, metadata, occurred_at)
                VALUES ('refund_requested', %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP);
                """,
                (
                    customer_id,
                    order_id,
                    refund_id,
                    json.dumps({"amount": float(amount_dec), "reason": reason}),
                ),
            )
            return refund_id

    if conn is not None:
        return _execute(conn)
    else:
        with get_connection() as managed_conn:
            return _execute(managed_conn)


def approve_refund_request(
    refund_id: int,
    reviewer: str = "governance_agent",
    conn=None,
) -> bool:
    """Transition a requested refund to 'approved' status."""
    def _execute(connection) -> bool:
        with get_transaction(connection) as cur:
            cur.execute(
                """
                SELECT status FROM refunds WHERE refund_id = %s FOR UPDATE;
                """,
                (refund_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Refund #{refund_id} not found.")
            current_status = row[0]
            if current_status not in ("requested", "pending"):
                raise RefundTransactionError(f"Cannot approve refund #{refund_id} with status '{current_status}'.")

            cur.execute(
                """
                UPDATE refunds
                SET status = 'approved',
                    reviewed_at = CURRENT_TIMESTAMP,
                    reviewed_by = %s
                WHERE refund_id = %s;
                """,
                (reviewer, refund_id),
            )
            return True

    if conn is not None:
        return _execute(conn)
    else:
        with get_connection() as managed_conn:
            return _execute(managed_conn)


def execute_refund(
    refund_id: int,
    idempotency_key: str,
    reviewer: str = "governance_executor",
    restock_inventory: bool = True,
    _inject_failure_before_commit: bool = False,
    conn=None,
) -> dict:
    """Atomically execute an approved refund in PostgreSQL.
    
    Performs all necessary mutations in a single ACID transaction:
    1. Locks refund row and validates status is 'approved' or 'requested'.
    2. Enforces idempotency key uniqueness.
    3. Re-evaluates eligibility against business rules.
    4. Updates refund status to 'processed' with timestamp and idempotency key.
    5. Updates order status and payment status.
    6. Updates inventory returned quantities if restock is enabled.
    7. Inserts audit event into events table.
    8. Rolls back atomically on any error.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("A valid idempotency_key is required for refund execution.")

    def _execute(connection) -> dict:
        with get_transaction(connection) as cur:
            # 1. Check idempotency key for any already-processed refund
            cur.execute(
                "SELECT refund_id, status FROM refunds WHERE idempotency_key = %s;",
                (idempotency_key,),
            )
            existing_key_row = cur.fetchone()
            if existing_key_row:
                existing_id, existing_status = existing_key_row
                raise DuplicateRefundExecutionError(
                    f"Idempotency key '{idempotency_key}' has already been executed for refund #{existing_id} "
                    f"(status: {existing_status}). Duplicate execution prevented."
                )

            # 2. Lock and fetch target refund record
            cur.execute(
                """
                SELECT refund_id, order_id, customer_id, amount, status
                FROM refunds
                WHERE refund_id = %s
                FOR UPDATE;
                """,
                (refund_id,),
            )
            refund_row = cur.fetchone()
            if not refund_row:
                raise ValueError(f"Refund record #{refund_id} not found.")

            _, order_id, customer_id, amount, status = refund_row
            amount_dec = Decimal(str(amount))

            if status in ("processed", "executed"):
                raise DuplicateRefundExecutionError(
                    f"Refund #{refund_id} has already been executed (status: '{status}'). Duplicate execution prevented."
                )

            if status not in ("approved", "requested"):
                raise RefundTransactionError(
                    f"Refund #{refund_id} cannot be executed from status '{status}'. Must be 'approved' or 'requested'."
                )

            # 3. Verify eligibility before execution, excluding this refund from prior sum
            eligibility = check_refund_eligibility(
                order_id=order_id,
                requested_amount=amount_dec,
                exclude_refund_id=refund_id,
                conn=connection,
            )
            if not eligibility.is_eligible:
                raise RefundIneligibleError(
                    f"Cannot execute refund #{refund_id}: Order #{order_id} is ineligible: {eligibility.reason}"
                )

            # 4. Update refund record
            cur.execute(
                """
                UPDATE refunds
                SET status = 'processed',
                    processed_at = CURRENT_TIMESTAMP,
                    reviewed_by = %s,
                    idempotency_key = %s
                WHERE refund_id = %s;
                """,
                (reviewer, idempotency_key, refund_id),
            )

            # 5. Calculate cumulative refunds and update order
            cur.execute(
                """
                SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0.00)
                FROM order_items oi
                WHERE oi.order_id = %s;
                """,
                (order_id,),
            )
            total_order_amount = Decimal(str(cur.fetchone()[0]))

            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0.00)
                FROM refunds
                WHERE order_id = %s AND status IN ('approved', 'processed', 'executed');
                """,
                (order_id,),
            )
            total_refunded_now = Decimal(str(cur.fetchone()[0]))

            if total_refunded_now >= total_order_amount:
                new_payment_status = "refunded"
                new_order_status = "returned"
            else:
                new_payment_status = "partially_refunded"
                new_order_status = "delivered"

            cur.execute(
                """
                UPDATE orders
                SET payment_status = %s,
                    order_status = %s
                WHERE order_id = %s;
                """,
                (new_payment_status, new_order_status, order_id),
            )

            # 6. Update inventory (restock policy)
            if restock_inventory:
                cur.execute(
                    """
                    SELECT product_id, quantity
                    FROM order_items
                    WHERE order_id = %s;
                    """,
                    (order_id,),
                )
                items = cur.fetchall()
                for prod_id, qty in items:
                    cur.execute(
                        """
                        UPDATE inventory
                        SET quantity_returned = quantity_returned + %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE product_id = %s;
                        """,
                        (qty, prod_id),
                    )

            # 7. Insert audit event
            cur.execute(
                """
                INSERT INTO events (event_type, customer_id, order_id, refund_id, metadata, occurred_at)
                VALUES ('refund_processed', %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP);
                """,
                (
                    customer_id,
                    order_id,
                    refund_id,
                    json.dumps(
                        {
                            "amount": float(amount_dec),
                            "idempotency_key": idempotency_key,
                            "reviewer": reviewer,
                            "restocked": restock_inventory,
                            "payment_status": new_payment_status,
                        }
                    ),
                ),
            )

            # Simulated failure hook for rollback tests
            if _inject_failure_before_commit:
                raise RuntimeError("Simulated transaction failure injected for testing atomic rollback.")

            return {
                "refund_id": refund_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "amount": float(amount_dec),
                "status": "processed",
                "payment_status": new_payment_status,
                "idempotency_key": idempotency_key,
            }

    if conn is not None:
        return _execute(conn)
    else:
        with get_connection() as managed_conn:
            return _execute(managed_conn)
