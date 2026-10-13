"""Context acquisition and cross-entity correlation tools backed by PostgreSQL.

Provides:
- context.get_risk_signals: Query stored risk signals by customer, order, or product
- context.get_recent_events: Query recent timeline events across entities
- context.get_aggregate_activity: Multi-entity correlation (detects product return surges,
  shared device/shipping fingerprints across multiple accounts, and mass defect patterns)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.models import ActionCategory, RiskLevel, ToolDefinition
from customer_support.registry import ToolRegistry, get_registry
from database.connection import get_connection

logger = logging.getLogger(__name__)


# ============================================================================
# Argument Schemas
# ============================================================================

class GetRiskSignalsInput(BaseModel):
    """Input arguments for querying stored risk signals."""
    customer_id: Optional[int] = Field(None, description="Customer ID filter")
    order_id: Optional[int] = Field(None, description="Order ID filter")
    product_id: Optional[int] = Field(None, description="Product ID filter")
    signal_type: Optional[str] = Field(None, description="Specific risk signal type code")
    limit: int = Field(20, ge=1, le=100, description="Max signals to return")


class GetRecentEventsInput(BaseModel):
    """Input arguments for querying recent timeline events."""
    event_type: Optional[str] = Field(None, description="Event type name filter")
    customer_id: Optional[int] = Field(None, description="Customer ID filter")
    order_id: Optional[int] = Field(None, description="Order ID filter")
    product_id: Optional[int] = Field(None, description="Product ID filter")
    days: int = Field(30, ge=1, le=365, description="Lookback window in days")
    limit: int = Field(25, ge=1, le=100, description="Max events to return")


class GetAggregateActivityInput(BaseModel):
    """Input arguments for multi-entity correlation and pattern detection."""
    product_id: Optional[int] = Field(None, description="Product ID to evaluate for return concentration")
    customer_id: Optional[int] = Field(None, description="Customer ID to evaluate for shared fingerprint clusters")
    days: int = Field(30, ge=1, le=180, description="Time window in days")


# ============================================================================
# Tool Implementations
# ============================================================================

def get_risk_signals(
    customer_id: Optional[int] = None,
    order_id: Optional[int] = None,
    product_id: Optional[int] = None,
    signal_type: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Retrieve stored algorithmic risk signals matching criteria."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT signal_id, customer_id, order_id, product_id, signal_type,
                       signal_value, severity, confidence, source, created_at
                FROM risk_signals
                WHERE (%s IS NULL OR customer_id = %s)
                  AND (%s IS NULL OR order_id = %s)
                  AND (%s IS NULL OR product_id = %s)
                  AND (%s IS NULL OR signal_type = %s)
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (customer_id, customer_id, order_id, order_id, product_id, product_id, signal_type, signal_type, limit),
            )
            rows = cur.fetchall()
            signals = [
                {
                    "signal_id": r[0],
                    "customer_id": r[1],
                    "order_id": r[2],
                    "product_id": r[3],
                    "signal_type": r[4],
                    "signal_value": r[5],
                    "severity": r[6],
                    "confidence": float(r[7]) if r[7] is not None else None,
                    "source": r[8],
                    "created_at": r[9].isoformat() if r[9] else None,
                }
                for r in rows
            ]
            return {
                "found": len(signals) > 0,
                "count": len(signals),
                "signals": signals,
            }


def get_recent_events(
    event_type: Optional[str] = None,
    customer_id: Optional[int] = None,
    order_id: Optional[int] = None,
    product_id: Optional[int] = None,
    days: int = 30,
    limit: int = 25,
) -> Dict[str, Any]:
    """Query recent operational and audit events across entities."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, event_type, customer_id, order_id, product_id,
                       refund_id, campaign_id, metadata, occurred_at
                FROM events
                WHERE (%s IS NULL OR event_type = %s)
                  AND (%s IS NULL OR customer_id = %s)
                  AND (%s IS NULL OR order_id = %s)
                  AND (%s IS NULL OR product_id = %s)
                  AND occurred_at >= CURRENT_TIMESTAMP - (%s || ' days')::interval
                ORDER BY occurred_at DESC
                LIMIT %s;
                """,
                (event_type, event_type, customer_id, customer_id, order_id, order_id, product_id, product_id, str(days), limit),
            )
            rows = cur.fetchall()
            events = [
                {
                    "event_id": r[0],
                    "event_type": r[1],
                    "customer_id": r[2],
                    "order_id": r[3],
                    "product_id": r[4],
                    "refund_id": r[5],
                    "campaign_id": r[6],
                    "metadata": r[7],
                    "occurred_at": r[8].isoformat() if r[8] else None,
                }
                for r in rows
            ]
            return {
                "found": len(events) > 0,
                "count": len(events),
                "lookback_days": days,
                "events": events,
            }


def get_aggregate_activity(
    product_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """Correlate metrics across entities: return surge detection and shared fingerprint clusters."""
    result: Dict[str, Any] = {
        "lookback_days": days,
        "product_correlation": None,
        "customer_cluster_correlation": None,
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Product-level aggregate correlation
            if product_id is not None:
                cur.execute(
                    """
                    SELECT p.product_id, p.name, p.is_limited_edition,
                           COALESCE(SUM(oi.quantity), 0) AS total_units_ordered
                    FROM products p
                    LEFT JOIN order_items oi ON p.product_id = oi.product_id
                    LEFT JOIN orders o ON oi.order_id = o.order_id
                        AND o.placed_at >= CURRENT_TIMESTAMP - (%s || ' days')::interval
                    WHERE p.product_id = %s
                    GROUP BY p.product_id, p.name, p.is_limited_edition;
                    """,
                    (str(days), product_id),
                )
                prod_row = cur.fetchone()
                if prod_row:
                    prod_id, prod_name, is_le, units_ordered = prod_row

                    # Count units returned and refund requests for this product
                    cur.execute(
                        """
                        SELECT COUNT(DISTINCT r.refund_id),
                               COUNT(DISTINCT r.customer_id),
                               COALESCE(SUM(oi.quantity), 0)
                        FROM refunds r
                        JOIN orders o ON r.order_id = o.order_id
                        JOIN order_items oi ON o.order_id = oi.order_id
                        WHERE oi.product_id = %s
                          AND r.requested_at >= CURRENT_TIMESTAMP - (%s || ' days')::interval;
                        """,
                        (product_id, str(days)),
                    )
                    ref_row = cur.fetchone()
                    refund_count = ref_row[0] if ref_row else 0
                    distinct_refunding_customers = ref_row[1] if ref_row else 0
                    units_returned_window = ref_row[2] if ref_row else 0

                    # Defect signals count
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM risk_signals
                        WHERE product_id = %s
                          AND signal_type = 'defective_batch'
                          AND created_at >= CURRENT_TIMESTAMP - (%s || ' days')::interval;
                        """,
                        (product_id, str(days)),
                    )
                    defect_signals = cur.fetchone()[0]

                    return_ratio = (
                        (units_returned_window / units_ordered * 100.0)
                        if units_ordered > 0
                        else 0.0
                    )

                    result["product_correlation"] = {
                        "product_id": prod_id,
                        "product_name": prod_name,
                        "is_limited_edition": bool(is_le),
                        "units_ordered_in_window": int(units_ordered),
                        "refund_requests_in_window": int(refund_count),
                        "distinct_customers_requesting_refund": int(distinct_refunding_customers),
                        "return_percentage_in_window": round(return_ratio, 2),
                        "defective_batch_signals": int(defect_signals),
                        "is_mass_defect_indicated": defect_signals > 5 or distinct_refunding_customers > 20,
                    }

            # 2. Customer-level cluster correlation
            if customer_id is not None:
                # Find customer's device fingerprints and shipping hashes
                cur.execute(
                    """
                    SELECT DISTINCT cs.device_fingerprint, o.shipping_address_hash, o.payment_fingerprint
                    FROM customers c
                    LEFT JOIN customer_sessions cs ON c.customer_id = cs.customer_id
                    LEFT JOIN orders o ON c.customer_id = o.customer_id
                    WHERE c.customer_id = %s;
                    """,
                    (customer_id,),
                )
                fingerprints = cur.fetchall()
                device_fps = {r[0] for r in fingerprints if r[0]}
                shipping_hashes = {r[1] for r in fingerprints if r[1]}
                payment_fps = {r[2] for r in fingerprints if r[2]}

                shared_accounts: set[int] = set()

                if device_fps:
                    cur.execute(
                        """
                        SELECT DISTINCT customer_id
                        FROM customer_sessions
                        WHERE device_fingerprint = ANY(%s) AND customer_id != %s;
                        """,
                        (list(device_fps), customer_id),
                    )
                    shared_accounts.update(r[0] for r in cur.fetchall())

                if shipping_hashes:
                    cur.execute(
                        """
                        SELECT DISTINCT customer_id
                        FROM orders
                        WHERE shipping_address_hash = ANY(%s) AND customer_id != %s;
                        """,
                        (list(shipping_hashes), customer_id),
                    )
                    shared_accounts.update(r[0] for r in cur.fetchall())

                if payment_fps:
                    cur.execute(
                        """
                        SELECT DISTINCT customer_id
                        FROM orders
                        WHERE payment_fingerprint = ANY(%s) AND customer_id != %s;
                        """,
                        (list(payment_fps), customer_id),
                    )
                    shared_accounts.update(r[0] for r in cur.fetchall())

                # Count refunds across the cluster
                cluster_refund_count = 0
                if shared_accounts:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM refunds
                        WHERE customer_id = ANY(%s)
                          AND requested_at >= CURRENT_TIMESTAMP - (%s || ' days')::interval;
                        """,
                        (list(shared_accounts), str(days)),
                    )
                    cluster_refund_count = cur.fetchone()[0]

                result["customer_cluster_correlation"] = {
                    "customer_id": customer_id,
                    "shared_fingerprint_accounts_count": len(shared_accounts),
                    "shared_customer_ids": sorted(list(shared_accounts))[:10],
                    "cluster_refund_requests_in_window": cluster_refund_count,
                    "is_coordinated_syndicate_indicated": len(shared_accounts) >= 3 and cluster_refund_count >= 5,
                }

    return result


# ============================================================================
# Tool Registration
# ============================================================================

def register_context_tools(reg: Optional[ToolRegistry] = None) -> None:
    """Register all context acquisition tools into target tool registry."""
    target_reg = reg or get_registry()

    target_reg.register(
        ToolDefinition(
            name="context.get_risk_signals",
            description="Retrieve automated risk signals recorded for customer, order, or product.",
            arg_schema=GetRiskSignalsInput,
            function=get_risk_signals,
            permissions=["context.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="context.get_recent_events",
            description="Query recent timeline events across customers, orders, and products within a time window.",
            arg_schema=GetRecentEventsInput,
            function=get_recent_events,
            permissions=["context.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )

    target_reg.register(
        ToolDefinition(
            name="context.get_aggregate_activity",
            description="Correlate multi-entity business metrics: detects product return surges, mass-defect patterns, and multi-account device/shipping fingerprint overlaps.",
            arg_schema=GetAggregateActivityInput,
            function=get_aggregate_activity,
            permissions=["context.read"],
            risk_level=RiskLevel.LOW,
            is_mutation=False,
            side_effect=False,
            reversible=True,
            action_category=ActionCategory.READ,
        )
    )
