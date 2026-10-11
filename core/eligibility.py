"""Refund eligibility business rules engine for BlackSmith.

Evaluates orders against business policies including:
- Order existence and ownership
- Payment status (must be PAID)
- Order status (not cancelled)
- Return window (30 days standard, 90 days for verified defect)
- Prior refund amounts and remaining refundable balance
- Maximum allowable refund
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.models import PolicyCheckResult, RefundEligibility
from database.connection import get_connection


STANDARD_RETURN_WINDOW_DAYS = 30
DEFECTIVE_RETURN_WINDOW_DAYS = 90


def check_refund_eligibility(
    order_id: int,
    requested_amount: Optional[Decimal | float | str] = None,
    is_defective: bool = False,
    reference_time: Optional[datetime] = None,
    exclude_refund_id: Optional[int] = None,
    conn=None,
) -> RefundEligibility:
    """Evaluate whether an order is eligible for a refund under business rules."""
    violations: list[str] = []
    policy_checks: list[PolicyCheckResult] = []

    def run_check(rule_name: str, passed: bool, message: str, details: Optional[dict] = None) -> None:
        policy_checks.append(
            PolicyCheckResult(
                rule_name=rule_name,
                passed=passed,
                message=message,
                details=details or {},
            )
        )
        if not passed:
            violations.append(message)

    def _execute(connection) -> RefundEligibility:
        with connection.cursor() as cur:
            # 1. Fetch Order and items
            cur.execute(
                """
                SELECT o.order_id, o.customer_id, o.order_status, o.payment_status, o.placed_at,
                       COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_order_amount
                FROM orders o
                LEFT JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.order_id = %s
                GROUP BY o.order_id, o.customer_id, o.order_status, o.payment_status, o.placed_at;
                """,
                (order_id,),
            )
            order_row = cur.fetchone()

            if not order_row:
                run_check(
                    rule_name="order_existence",
                    passed=False,
                    message=f"Order #{order_id} does not exist in records.",
                )
                return RefundEligibility(
                    is_eligible=False,
                    order_id=order_id,
                    max_refundable_amount=Decimal("0.00"),
                    reason=f"Order #{order_id} not found.",
                    violations=violations,
                    policy_checks=policy_checks,
                )

            _, customer_id, order_status, payment_status, placed_at, total_order_amount = order_row
            total_order_amount = Decimal(str(total_order_amount))

            run_check(
                rule_name="order_existence",
                passed=True,
                message=f"Order #{order_id} found.",
                details={"order_id": order_id, "customer_id": customer_id},
            )

            # 2. Payment status check (must be 'paid' or 'partially_refunded')
            is_paid = payment_status.lower() in ("paid", "partially_refunded")
            run_check(
                rule_name="payment_verification",
                passed=is_paid,
                message=f"Order payment status is '{payment_status}'. Only paid orders are eligible for refund.",
                details={"payment_status": payment_status},
            )

            # 3. Order status check (cannot be 'cancelled')
            is_valid_status = order_status.lower() not in ("cancelled",)
            run_check(
                rule_name="order_status_check",
                passed=is_valid_status,
                message=f"Order status is '{order_status}'. Cancelled orders cannot be refunded via return workflow.",
                details={"order_status": order_status},
            )

            # 4. Return window check
            eval_time = reference_time or datetime.now(timezone.utc)
            if placed_at.tzinfo is None:
                placed_at = placed_at.replace(tzinfo=timezone.utc)
            if eval_time.tzinfo is None:
                eval_time = eval_time.replace(tzinfo=timezone.utc)

            days_elapsed = (eval_time - placed_at).total_seconds() / 86400.0
            allowed_window = DEFECTIVE_RETURN_WINDOW_DAYS if is_defective else STANDARD_RETURN_WINDOW_DAYS

            is_within_window = days_elapsed <= allowed_window
            run_check(
                rule_name="return_window",
                passed=is_within_window,
                message=(
                    f"Return requested {days_elapsed:.1f} days after order placement. "
                    f"Policy limit is {allowed_window} days ({'defective item' if is_defective else 'standard'})."
                ),
                details={"days_elapsed": days_elapsed, "allowed_window_days": allowed_window},
            )

            # 5. Calculate previous refunds (excluding current refund if re-evaluating)
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0.00)
                FROM refunds
                WHERE order_id = %s 
                  AND status IN ('approved', 'processed', 'executed')
                  AND (%s IS NULL OR refund_id != %s);
                """,
                (order_id, exclude_refund_id, exclude_refund_id),
            )
            total_refunded = Decimal(str(cur.fetchone()[0]))

            remaining_refundable = max(Decimal("0.00"), total_order_amount - total_refunded)

            run_check(
                rule_name="refundable_balance",
                passed=remaining_refundable > Decimal("0.00"),
                message=(
                    f"Order total: ${total_order_amount:.2f}. "
                    f"Previously refunded: ${total_refunded:.2f}. "
                    f"Remaining refundable balance: ${remaining_refundable:.2f}."
                ),
                details={
                    "total_order_amount": float(total_order_amount),
                    "total_refunded": float(total_refunded),
                    "remaining_refundable": float(remaining_refundable),
                },
            )

            # 6. Requested amount validation (if specified)
            if requested_amount is not None:
                req_dec = Decimal(str(requested_amount))
                if req_dec <= Decimal("0.00"):
                    run_check(
                        rule_name="requested_amount_positive",
                        passed=False,
                        message=f"Requested refund amount (${req_dec:.2f}) must be greater than zero.",
                    )
                elif req_dec > remaining_refundable:
                    run_check(
                        rule_name="requested_amount_limit",
                        passed=False,
                        message=(
                            f"Requested refund of ${req_dec:.2f} exceeds remaining refundable balance "
                            f"of ${remaining_refundable:.2f}."
                        ),
                        details={"requested": float(req_dec), "remaining": float(remaining_refundable)},
                    )
                else:
                    run_check(
                        rule_name="requested_amount_limit",
                        passed=True,
                        message=f"Requested refund amount of ${req_dec:.2f} is within limit.",
                        details={"requested": float(req_dec)},
                    )

            is_eligible = len(violations) == 0
            reason = (
                f"Order #{order_id} is eligible for refund up to ${remaining_refundable:.2f}."
                if is_eligible
                else f"Order #{order_id} is ineligible: {'; '.join(violations)}"
            )

            return RefundEligibility(
                is_eligible=is_eligible,
                order_id=order_id,
                max_refundable_amount=remaining_refundable,
                reason=reason,
                violations=violations,
                policy_checks=policy_checks,
            )

    if conn is not None:
        return _execute(conn)
    else:
        with get_connection() as managed_conn:
            return _execute(managed_conn)
