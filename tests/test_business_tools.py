"""Integration tests for all database-backed business tools (§7 & §23.1).

Tests all 23 business tools through the canonical ToolRegistry against
live PostgreSQL database records without relying on an LLM.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Generator
import pytest

from customer_support.registry import registry
from database.connection import get_connection, get_transaction


@pytest.fixture(scope="module")
def seeded_customer_and_order():
    """Retrieve an existing customer and order with known items from seeded database."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.customer_id, c.email, o.order_id, p.product_id, p.sku
                FROM customers c
                JOIN orders o ON c.customer_id = o.customer_id
                JOIN order_items oi ON o.order_id = oi.order_id
                JOIN products p ON oi.product_id = p.product_id
                WHERE o.payment_status = 'paid'
                LIMIT 1;
                """
            )
            row = cur.fetchone()
            assert row is not None, "Seeded data must contain at least one customer with a paid order."
            return {
                "customer_id": row[0],
                "email": row[1],
                "order_id": row[2],
                "product_id": row[3],
                "sku": row[4],
            }


# ============================================================================
# 1. Customer Tools Tests
# ============================================================================

def test_customer_get_tool(seeded_customer_and_order):
    """customer.get retrieves customer by ID and email, and fails safely on missing IDs."""
    cid = seeded_customer_and_order["customer_id"]
    email = seeded_customer_and_order["email"]

    # By ID
    res1 = registry.execute("customer.get", {"customer_id": cid})
    assert res1.success is True
    assert res1.output["found"] is True
    assert res1.output["customer_id"] == cid
    assert res1.output["email"] == email

    # By Email
    res2 = registry.execute("customer.get", {"email": email})
    assert res2.success is True
    assert res2.output["found"] is True
    assert res2.output["customer_id"] == cid

    # Non-existent ID fails safely
    res_missing = registry.execute("customer.get", {"customer_id": 9999999})
    assert res_missing.success is True
    assert res_missing.output["found"] is False


def test_customer_orders_tool(seeded_customer_and_order):
    """customer.orders retrieves orders with amounts and item counts."""
    cid = seeded_customer_and_order["customer_id"]
    res = registry.execute("customer.orders", {"customer_id": cid, "limit": 5})

    assert res.success is True
    assert res.output["found"] is True
    assert res.output["order_count"] >= 1
    orders = res.output["orders"]
    assert len(orders) >= 1
    first_order = orders[0]
    assert "order_id" in first_order
    assert "total_amount" in first_order
    assert "item_count" in first_order


def test_customer_activity_tool(seeded_customer_and_order):
    """customer.activity returns combined activity streams."""
    cid = seeded_customer_and_order["customer_id"]
    res = registry.execute("customer.activity", {"customer_id": cid, "limit": 10})

    assert res.success is True
    assert res.output["found"] is True
    assert "sessions" in res.output
    assert "tickets" in res.output
    assert "messages" in res.output
    assert "events" in res.output


# ============================================================================
# 2. Order Tools Tests
# ============================================================================

def test_order_get_tool(seeded_customer_and_order):
    """order.get retrieves order summary and calculates total amount."""
    oid = seeded_customer_and_order["order_id"]
    res = registry.execute("order.get", {"order_id": oid})

    assert res.success is True
    assert res.output["found"] is True
    assert res.output["order_id"] == oid
    assert res.output["total_amount"] > 0
    assert "payment_status" in res.output


def test_order_items_tool(seeded_customer_and_order):
    """order.items retrieves line items with products and quantities."""
    oid = seeded_customer_and_order["order_id"]
    res = registry.execute("order.items", {"order_id": oid})

    assert res.success is True
    assert res.output["found"] is True
    assert res.output["item_count"] >= 1
    item = res.output["items"][0]
    assert "product_name" in item
    assert "quantity" in item
    assert "unit_price" in item


def test_order_status_tool(seeded_customer_and_order):
    """order.status retrieves fulfillment status."""
    oid = seeded_customer_and_order["order_id"]
    res = registry.execute("order.status", {"order_id": oid})

    assert res.success is True
    assert res.output["found"] is True
    assert res.output["order_id"] == oid
    assert "order_status" in res.output
    assert "payment_status" in res.output


def test_order_refundable_balance_tool(seeded_customer_and_order):
    """order.refundable_balance computes total and remaining allowable refund."""
    oid = seeded_customer_and_order["order_id"]
    res = registry.execute("order.refundable_balance", {"order_id": oid})

    assert res.success is True
    assert res.output["found"] is True
    assert res.output["total_order_amount"] > 0
    assert res.output["remaining_refundable_balance"] >= 0.0


# ============================================================================
# 3. Product and Commerce Tools Tests
# ============================================================================

def test_product_get_tool(seeded_customer_and_order):
    """product.get retrieves product by ID and SKU."""
    pid = seeded_customer_and_order["product_id"]
    sku = seeded_customer_and_order["sku"]

    # By ID
    res1 = registry.execute("product.get", {"product_id": pid})
    assert res1.success is True
    assert res1.output["found"] is True
    assert res1.output["product_id"] == pid

    # By SKU
    res2 = registry.execute("product.get", {"sku": sku})
    assert res2.success is True
    assert res2.output["found"] is True
    assert res2.output["sku"] == sku


def test_inventory_get_tool(seeded_customer_and_order):
    """inventory.get distinguishes total manufactured from current available stock."""
    pid = seeded_customer_and_order["product_id"]
    res = registry.execute("inventory.get", {"product_id": pid})

    assert res.success is True
    assert res.output["found"] is True
    assert "quantity_total_manufactured" in res.output
    assert "quantity_sold" in res.output
    assert "quantity_returned" in res.output
    assert "quantity_available" in res.output


def test_campaign_and_stats_tools():
    """campaign.get and commerce.campaign_stats inspect campaign and return statistics."""
    # Campaign 1 exists from seed data
    res_camp = registry.execute("campaign.get", {"campaign_id": 1})
    assert res_camp.success is True
    assert res_camp.output["found"] is True
    assert res_camp.output["campaign_id"] == 1

    res_stats = registry.execute("commerce.campaign_stats", {"campaign_id": 1})
    assert res_stats.success is True
    assert res_stats.output["found"] is True
    assert "return_rate_percentage" in res_stats.output
    assert "is_elevated_return_rate" in res_stats.output


# ============================================================================
# 4. Refund Tools Tests
# ============================================================================

def test_refund_history_and_check_eligibility(seeded_customer_and_order):
    """refund.history and refund.check_eligibility execute read-only queries."""
    oid = seeded_customer_and_order["order_id"]

    # Eligibility evaluation without mutation
    res_elig = registry.execute("refund.check_eligibility", {"order_id": oid})
    assert res_elig.success is True
    assert "is_eligible" in res_elig.output
    assert "policy_checks" in res_elig.output

    # Calculate amount without mutation
    res_calc = registry.execute("refund.calculate_amount", {"order_id": oid})
    assert res_calc.success is True
    assert res_calc.output["found"] is True
    assert res_calc.output["max_allowable_refund"] >= 0.0

    # History
    res_hist = registry.execute("refund.history", {"order_id": oid})
    assert res_hist.success is True
    assert "refunds" in res_hist.output


def test_refund_workflow_lifecycle():
    """Test full refund tool lifecycle: create_request -> approve_request -> execute and reject_request."""
    # Create temporary order for lifecycle testing
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT product_id, price FROM products LIMIT 1;")
            prod_id, price = cur.fetchone()

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
                VALUES (%s, %s, 1, %s);
                """,
                (order_id, prod_id, price),
            )
        conn.commit()

    try:
        # 1. refund.create_request
        req_res = registry.execute(
            "refund.create_request",
            {
                "order_id": order_id,
                "customer_id": 1,
                "amount": float(price),
                "reason": "Test tool lifecycle refund request",
            },
        )
        assert req_res.success is True
        assert req_res.mutation_performed is True
        refund_id = req_res.output["refund_id"]
        assert req_res.output["status"] == "requested"

        # 2. refund.approve_request
        appr_res = registry.execute(
            "refund.approve_request",
            {"refund_id": refund_id, "reviewer": "test_supervisor"},
        )
        assert appr_res.success is True
        assert appr_res.mutation_performed is True
        assert appr_res.output["status"] == "approved"

        # 3. refund.execute
        idempotency_token = f"idem-tool-{uuid.uuid4()}"
        exec_res = registry.execute(
            "refund.execute",
            {
                "refund_id": refund_id,
                "idempotency_key": idempotency_token,
                "reviewer": "test_executor",
                "restock_inventory": True,
            },
        )
        assert exec_res.success is True
        assert exec_res.mutation_performed is True
        assert exec_res.output["status"] == "processed"
        assert exec_res.output["payment_status"] in ("refunded", "partially_refunded")

        # 4. Create second refund to test refund.reject_request
        req_res2 = registry.execute(
            "refund.create_request",
            {
                "order_id": order_id,
                "customer_id": 1,
                "amount": 10.0,
                "reason": "Request to test rejection tool",
            },
        )
        refund_id2 = req_res2.output["refund_id"]

        rej_res = registry.execute(
            "refund.reject_request",
            {
                "refund_id": refund_id2,
                "reason": "Policy violation: outside parameters",
                "reviewer": "test_supervisor",
            },
        )
        assert rej_res.success is True
        assert rej_res.output["status"] == "rejected"
        assert rej_res.output["reason"] == "Policy violation: outside parameters"

    finally:
        # Clean up temporary test data
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM events WHERE order_id = %s;", (order_id,))
                cur.execute("DELETE FROM refunds WHERE order_id = %s;", (order_id,))
                cur.execute("DELETE FROM order_items WHERE order_id = %s;", (order_id,))
                cur.execute("DELETE FROM orders WHERE order_id = %s;", (order_id,))
            conn.commit()


# ============================================================================
# 5. Communication Tools Tests
# ============================================================================

def test_communication_tools():
    """communication.get_messages and ticket.save_draft execute properly."""
    # Query messages
    msg_res = registry.execute("communication.get_messages", {"customer_id": 1, "limit": 5})
    assert msg_res.success is True
    assert "messages" in msg_res.output

    # Save draft on benchmark ticket #1
    draft_text = "Hello! We are currently investigating your order delivery status."
    draft_res = registry.execute(
        "ticket.save_draft",
        {"ticket_id": 1, "response_draft": draft_text},
    )
    assert draft_res.success is True
    assert draft_res.mutation_performed is True
    assert draft_res.output["saved"] is True
    assert draft_res.output["response_draft"] == draft_text


# ============================================================================
# 6. Context Tools Tests
# ============================================================================

def test_context_tools():
    """context.get_risk_signals, context.get_recent_events, and context.get_aggregate_activity."""
    # Risk signals
    sig_res = registry.execute("context.get_risk_signals", {"limit": 10})
    assert sig_res.success is True
    assert "signals" in sig_res.output
    assert sig_res.output["count"] >= 1

    # Recent events
    evt_res = registry.execute("context.get_recent_events", {"days": 90, "limit": 10})
    assert evt_res.success is True
    assert "events" in evt_res.output
    assert evt_res.output["count"] >= 1

    # Multi-entity aggregation: Limited edition earbuds product #1
    agg_res = registry.execute(
        "context.get_aggregate_activity",
        {"product_id": 1, "customer_id": 1, "days": 90},
    )
    assert agg_res.success is True
    assert agg_res.output["product_correlation"] is not None
    assert agg_res.output["customer_cluster_correlation"] is not None

    prod_corr = agg_res.output["product_correlation"]
    assert prod_corr["product_id"] == 1
    assert "return_percentage_in_window" in prod_corr

    cust_corr = agg_res.output["customer_cluster_correlation"]
    assert cust_corr["customer_id"] == 1
    assert "shared_fingerprint_accounts_count" in cust_corr
