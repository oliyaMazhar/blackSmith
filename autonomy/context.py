"""Targeted context acquisition for risk evaluation and governance (§12.2).

Performs conditional, narrow database-backed context queries based on the
action proposed, gathering evidence across entities while avoiding excessive data loading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core.eligibility import check_refund_eligibility
from core.models import ActionCategory, PolicyCheckResult, RefundEligibility, ToolDefinition
from database.connection import get_connection

logger = logging.getLogger(__name__)


@dataclass
class RiskContext:
    """Rich, structured business context assembled for risk evaluation."""
    tool_name: str
    action_category: ActionCategory
    arguments: Dict[str, Any]

    # Target identifiers
    order_id: Optional[int] = None
    customer_id: Optional[int] = None
    product_id: Optional[int] = None
    refund_id: Optional[int] = None

    # Customer standing
    customer_found: bool = False
    customer_status: str = "unknown"
    customer_identity_verified: bool = False
    customer_lifetime_refunds_count: int = 0
    customer_lifetime_refunds_amount: Decimal = Decimal("0.00")

    # Order details
    order_found: bool = False
    order_status: str = "unknown"
    payment_status: str = "unknown"
    order_total: Decimal = Decimal("0.00")
    order_placed_at: Optional[str] = None
    order_items: List[Dict[str, Any]] = field(default_factory=list)

    # Financial details
    proposed_amount: Decimal = Decimal("0.00")
    total_previously_refunded: Decimal = Decimal("0.00")
    remaining_refundable_balance: Decimal = Decimal("0.00")

    # Business rule eligibility
    eligibility_evaluated: bool = False
    is_eligible: bool = False
    eligibility_violations: List[str] = field(default_factory=list)
    policy_checks: List[PolicyCheckResult] = field(default_factory=list)

    # Product, Campaign & Inventory
    is_limited_edition: bool = False
    product_name: Optional[str] = None
    campaign_active: bool = False
    campaign_sold_out: bool = False
    campaign_production_quantity: int = 0
    product_units_sold: int = 0
    product_units_returned: int = 0
    product_return_rate_pct: float = 0.0

    # Cross-entity correlation & signals
    correlated_risk_signals: List[Dict[str, Any]] = field(default_factory=list)
    shared_fingerprint_accounts_count: int = 0
    shared_cluster_refund_count: int = 0
    defective_batch_reports_count: int = 0

    # Data completeness & gaps
    missing_critical_data: List[str] = field(default_factory=list)


def acquire_risk_context(
    tool: ToolDefinition,
    arguments: Dict[str, Any],
) -> RiskContext:
    """Conditionally acquire relevant business context for a proposed tool execution.
    
    Narrowly retrieves only the data required to assess the specific operation:
    - Read tools bypass heavy queries.
    - Financial mutations query order, eligibility, customer standing, and cross-account clusters.
    """
    ctx = RiskContext(
        tool_name=tool.name,
        action_category=tool.action_category,
        arguments=arguments,
    )

    # Extract target entity IDs from arguments
    ctx.order_id = arguments.get("order_id")
    ctx.customer_id = arguments.get("customer_id")
    ctx.product_id = arguments.get("product_id")
    ctx.refund_id = arguments.get("refund_id")

    # Parse proposed amount if present
    if "amount" in arguments and arguments["amount"] is not None:
        try:
            ctx.proposed_amount = Decimal(str(arguments["amount"]))
        except Exception:
            ctx.proposed_amount = Decimal("0.00")

    # For read-only tools or draft saving, minimal context is sufficient
    if not tool.is_mutation and tool.action_category == ActionCategory.READ:
        return ctx

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Resolve refund record if refund_id is provided
            if ctx.refund_id is not None:
                cur.execute(
                    """
                    SELECT order_id, customer_id, amount, status, reason
                    FROM refunds
                    WHERE refund_id = %s;
                    """,
                    (ctx.refund_id,),
                )
                r_row = cur.fetchone()
                if r_row:
                    if ctx.order_id is None:
                        ctx.order_id = r_row[0]
                    if ctx.customer_id is None:
                        ctx.customer_id = r_row[1]
                    if ctx.proposed_amount <= Decimal("0.00"):
                        ctx.proposed_amount = Decimal(str(r_row[2]))
                else:
                    ctx.missing_critical_data.append(f"Refund record #{ctx.refund_id} not found")

            # 2. Acquire Order context
            if ctx.order_id is not None:
                cur.execute(
                    """
                    SELECT o.order_id, o.customer_id, o.order_status, o.payment_status,
                           o.placed_at, o.campaign_id,
                           COALESCE(SUM(oi.quantity * oi.unit_price), 0.00) AS total_amount
                    FROM orders o
                    LEFT JOIN order_items oi ON o.order_id = oi.order_id
                    WHERE o.order_id = %s
                    GROUP BY o.order_id, o.customer_id, o.order_status, o.payment_status, o.placed_at, o.campaign_id;
                    """,
                    (ctx.order_id,),
                )
                o_row = cur.fetchone()
                if o_row:
                    ctx.order_found = True
                    if ctx.customer_id is None:
                        ctx.customer_id = o_row[1]
                    ctx.order_status = o_row[2]
                    ctx.payment_status = o_row[3]
                    ctx.order_placed_at = o_row[4].isoformat() if o_row[4] else None
                    campaign_id = o_row[5]
                    ctx.order_total = Decimal(str(o_row[6]))

                    # Fetch line items
                    cur.execute(
                        """
                        SELECT oi.order_item_id, oi.product_id, p.name, p.sku,
                               oi.quantity, oi.unit_price, p.is_limited_edition
                        FROM order_items oi
                        JOIN products p ON oi.product_id = p.product_id
                        WHERE oi.order_id = %s;
                        """,
                        (ctx.order_id,),
                    )
                    items = cur.fetchall()
                    ctx.order_items = [
                        {
                            "item_id": it[0],
                            "product_id": it[1],
                            "name": it[2],
                            "sku": it[3],
                            "quantity": it[4],
                            "unit_price": float(it[5]),
                            "is_limited_edition": bool(it[6]),
                        }
                        for it in items
                    ]
                    if items:
                        if ctx.product_id is None:
                            ctx.product_id = items[0][1]
                        ctx.is_limited_edition = any(it[6] for it in items)
                        ctx.product_name = items[0][2]

                    # Prior refunds on this order
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(amount), 0.00)
                        FROM refunds
                        WHERE order_id = %s AND status IN ('approved', 'processed', 'executed');
                        """,
                        (ctx.order_id,),
                    )
                    ctx.total_previously_refunded = Decimal(str(cur.fetchone()[0]))
                    ctx.remaining_refundable_balance = max(Decimal("0.00"), ctx.order_total - ctx.total_previously_refunded)

                    # Evaluate eligibility
                    is_defective = arguments.get("is_defective", False)
                    elig = check_refund_eligibility(
                        order_id=ctx.order_id,
                        requested_amount=ctx.proposed_amount if ctx.proposed_amount > Decimal("0.00") else None,
                        is_defective=is_defective,
                        exclude_refund_id=ctx.refund_id,
                        conn=conn,
                    )
                    ctx.eligibility_evaluated = True
                    ctx.is_eligible = elig.is_eligible
                    ctx.eligibility_violations = elig.violations
                    ctx.policy_checks = elig.policy_checks
                else:
                    ctx.missing_critical_data.append(f"Order #{ctx.order_id} does not exist")
            else:
                if tool.is_mutation:
                    ctx.missing_critical_data.append("No order_id supplied for financial mutation")

            # 3. Acquire Customer context
            if ctx.customer_id is not None:
                cur.execute(
                    """
                    SELECT customer_id, full_name, account_status, identity_verified
                    FROM customers
                    WHERE customer_id = %s;
                    """,
                    (ctx.customer_id,),
                )
                c_row = cur.fetchone()
                if c_row:
                    ctx.customer_found = True
                    ctx.customer_status = c_row[2]
                    ctx.customer_identity_verified = bool(c_row[3])

                    # Lifetime refunds by this customer
                    cur.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(amount), 0.00)
                        FROM refunds
                        WHERE customer_id = %s AND status IN ('approved', 'processed', 'executed');
                        """,
                        (ctx.customer_id,),
                    )
                    ref_agg = cur.fetchone()
                    ctx.customer_lifetime_refunds_count = ref_agg[0]
                    ctx.customer_lifetime_refunds_amount = Decimal(str(ref_agg[1]))
                else:
                    ctx.missing_critical_data.append(f"Customer #{ctx.customer_id} does not exist")

            # 4. Product, Inventory & Campaign context
            if ctx.product_id is not None:
                cur.execute(
                    """
                    SELECT p.is_limited_edition, p.name,
                           i.quantity_total, i.quantity_sold, i.quantity_returned
                    FROM products p
                    JOIN inventory i ON p.product_id = i.product_id
                    WHERE p.product_id = %s;
                    """,
                    (ctx.product_id,),
                )
                pi_row = cur.fetchone()
                if pi_row:
                    ctx.is_limited_edition = bool(pi_row[0])
                    ctx.product_name = pi_row[1]
                    total_mfg = pi_row[2]
                    sold_qty = pi_row[3]
                    ret_qty = pi_row[4]
                    ctx.product_units_sold = sold_qty
                    ctx.product_units_returned = ret_qty
                    ctx.product_return_rate_pct = (ret_qty / sold_qty * 100.0) if sold_qty > 0 else 0.0

                    # Check campaign state
                    cur.execute(
                        """
                        SELECT campaign_name, initial_quantity, status
                        FROM campaigns
                        WHERE product_id = %s
                        LIMIT 1;
                        """,
                        (ctx.product_id,),
                    )
                    camp_row = cur.fetchone()
                    if camp_row:
                        ctx.campaign_production_quantity = camp_row[1]
                        ctx.campaign_active = (camp_row[2] == "active")
                        ctx.campaign_sold_out = (sold_qty >= camp_row[1])

                # Query risk signals on product
                cur.execute(
                    """
                    SELECT signal_type, severity, confidence, source, signal_value
                    FROM risk_signals
                    WHERE product_id = %s
                    LIMIT 20;
                    """,
                    (ctx.product_id,),
                )
                for s_row in cur.fetchall():
                    ctx.correlated_risk_signals.append(
                        {
                            "signal_type": s_row[0],
                            "severity": s_row[1],
                            "confidence": float(s_row[2]) if s_row[2] else 1.0,
                            "source": s_row[3],
                            "value": s_row[4],
                        }
                    )

                # Check legitimate defect report volume
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT customer_id)
                    FROM support_tickets
                    WHERE category = 'DAMAGED_DEFECTIVE'
                      AND (order_id IN (SELECT order_id FROM order_items WHERE product_id = %s)
                           OR body ILIKE '%%defective%%' OR body ILIKE '%%audio%%');
                    """,
                    (ctx.product_id,),
                )
                ctx.defective_batch_reports_count = cur.fetchone()[0]

            # 5. Cross-account cluster & syndicate detection (shared fingerprint analysis)
            if ctx.customer_id is not None:
                cur.execute(
                    """
                    SELECT DISTINCT cs.device_fingerprint, o.shipping_address_hash, o.payment_fingerprint
                    FROM customers c
                    LEFT JOIN customer_sessions cs ON c.customer_id = cs.customer_id
                    LEFT JOIN orders o ON c.customer_id = o.customer_id
                    WHERE c.customer_id = %s;
                    """,
                    (ctx.customer_id,),
                )
                fps = cur.fetchall()
                dev_fps = [r[0] for r in fps if r[0]]
                ship_hashes = [r[1] for r in fps if r[1]]
                pay_fps = [r[2] for r in fps if r[2]]

                shared_cids: set[int] = set()
                if dev_fps:
                    cur.execute(
                        "SELECT DISTINCT customer_id FROM customer_sessions WHERE device_fingerprint = ANY(%s) AND customer_id != %s;",
                        (dev_fps, ctx.customer_id),
                    )
                    shared_cids.update(r[0] for r in cur.fetchall())

                if ship_hashes:
                    cur.execute(
                        "SELECT DISTINCT customer_id FROM orders WHERE shipping_address_hash = ANY(%s) AND customer_id != %s;",
                        (ship_hashes, ctx.customer_id),
                    )
                    shared_cids.update(r[0] for r in cur.fetchall())

                if pay_fps:
                    cur.execute(
                        "SELECT DISTINCT customer_id FROM orders WHERE payment_fingerprint = ANY(%s) AND customer_id != %s;",
                        (pay_fps, ctx.customer_id),
                    )
                    shared_cids.update(r[0] for r in cur.fetchall())

                ctx.shared_fingerprint_accounts_count = len(shared_cids)

                if shared_cids:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM refunds
                        WHERE customer_id = ANY(%s)
                          AND requested_at >= CURRENT_TIMESTAMP - INTERVAL '30 days';
                        """,
                        (list(shared_cids),),
                    )
                    ctx.shared_cluster_refund_count = cur.fetchone()[0]

    return ctx
