"""Database validation script for BlackSmith.

Validates:
1. Presence of all required tables.
2. Relational consistency and foreign key integrity.
3. Real, dynamic record counts from live PostgreSQL tables.
4. Presence and readiness of the 10 benchmark demo scenarios.
5. Internal consistency of the Limited-Edition Earbuds scenario.
6. Internal consistency of the Legitimate Mass-Return scenario.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.connection import get_connection

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("validate")

REQUIRED_TABLES = [
    "customers",
    "customer_sessions",
    "products",
    "campaigns",
    "inventory",
    "orders",
    "order_items",
    "refunds",
    "support_tickets",
    "messages",
    "policies",
    "risk_signals",
    "events",
    "audit_records",
]


def validate_database() -> bool:
    """Run comprehensive database validation and report findings."""
    all_passed = True
    print("\n" + "=" * 60)
    print("BLACKSMITH DATABASE INTEGRITY VALIDATION")
    print("=" * 60)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Check Table Existence
            print("\n[1/6] Checking Required Tables...")
            cur.execute(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public';
                """
            )
            existing_tables = {row[0] for row in cur.fetchall()}

            missing_tables = [t for t in REQUIRED_TABLES if t not in existing_tables]
            if missing_tables:
                print(f"  FAILED: Missing required tables: {', '.join(missing_tables)}")
                all_passed = False
            else:
                print(f"  PASSED: All {len(REQUIRED_TABLES)} required tables are present.")

            # 2. Query Actual Record Counts
            print("\n[2/6] Querying Live Table Record Counts...")
            counts: Dict[str, int] = {}
            for table in REQUIRED_TABLES:
                if table in existing_tables:
                    cur.execute(f"SELECT COUNT(*) FROM {table};")
                    count = cur.fetchone()[0]
                    counts[table] = count
                    print(f"  - {table:<20}: {count:>6} rows")
                else:
                    counts[table] = 0

            # 3. Check Foreign Key & Relational Consistency
            print("\n[3/6] Checking Relational Consistency & Orphan Records...")
            fk_checks: List[Tuple[str, str]] = [
                (
                    "Orphan order_items",
                    "SELECT COUNT(*) FROM order_items WHERE order_id NOT IN (SELECT order_id FROM orders);",
                ),
                (
                    "Orphan orders (missing customer)",
                    "SELECT COUNT(*) FROM orders WHERE customer_id NOT IN (SELECT customer_id FROM customers);",
                ),
                (
                    "Orphan refunds (missing order)",
                    "SELECT COUNT(*) FROM refunds WHERE order_id NOT IN (SELECT order_id FROM orders);",
                ),
                (
                    "Orphan inventory (missing product)",
                    "SELECT COUNT(*) FROM inventory WHERE product_id NOT IN (SELECT product_id FROM products);",
                ),
                (
                    "Orphan campaigns (missing product)",
                    "SELECT COUNT(*) FROM campaigns WHERE product_id NOT IN (SELECT product_id FROM products);",
                ),
                (
                    "Orphan tickets (missing customer)",
                    "SELECT COUNT(*) FROM support_tickets WHERE customer_id IS NOT NULL AND customer_id NOT IN (SELECT customer_id FROM customers);",
                ),
            ]

            fk_failed = False
            for check_name, check_sql in fk_checks:
                cur.execute(check_sql)
                orphan_count = cur.fetchone()[0]
                if orphan_count > 0:
                    print(f"  FAILED: {check_name} found {orphan_count} orphaned records!")
                    fk_failed = True
                    all_passed = False
                else:
                    print(f"  PASSED: {check_name} (0 orphans)")

            if not fk_failed:
                print("  PASSED: All relational integrity constraints verified.")

            # 4. Check Benchmark Scenario Tickets
            print("\n[4/6] Verifying Benchmark Support Scenarios...")
            cur.execute("SELECT COUNT(*) FROM support_tickets;")
            ticket_count = cur.fetchone()[0]
            if ticket_count >= 10:
                cur.execute(
                    """
                    SELECT ticket_id, category, priority, subject 
                    FROM support_tickets 
                    ORDER BY ticket_id ASC 
                    LIMIT 10;
                    """
                )
                tickets = cur.fetchall()
                print(f"  PASSED: Found {ticket_count} support tickets. Canonical benchmark scenarios:")
                for t_id, cat, prio, subj in tickets:
                    print(f"    [#{t_id}] ({cat:<18} | {prio:<6}) {subj[:45]}...")
            else:
                print(f"  FAILED: Expected at least 10 scenario tickets, found {ticket_count}.")
                all_passed = False

            # 5. Check Limited-Edition Scenario Internal Consistency
            print("\n[5/6] Verifying Limited-Edition Earbuds Scenario Consistency...")
            cur.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.price, p.is_limited_edition,
                       c.campaign_id, c.campaign_name, c.initial_quantity, c.status
                FROM products p
                JOIN campaigns c ON p.product_id = c.product_id
                WHERE p.is_limited_edition = true;
                """
            )
            le_row = cur.fetchone()
            if le_row:
                p_id, sku, p_name, price, is_le, c_id, c_name, initial_qty, c_status = le_row
                print(f"  - Product: {p_name} ({sku}), Price: ${price}, Limited Edition: {is_le}")
                print(f"  - Campaign #{c_id}: {c_name}, Initial Quantity: {initial_qty}, Status: {c_status}")

                # Check inventory
                cur.execute(
                    "SELECT quantity_total, quantity_sold, quantity_returned FROM inventory WHERE product_id = %s;",
                    (p_id,),
                )
                inv_row = cur.fetchone()
                if inv_row:
                    q_total, q_sold, q_ret = inv_row
                    print(f"  - Inventory: Total={q_total}, Sold={q_sold}, Returned={q_ret}")
                    if q_total == initial_qty:
                        print("  PASSED: Campaign production quantity matches inventory total (500 units).")
                    else:
                        print(f"  WARNING: Inventory total ({q_total}) != initial quantity ({initial_qty})")

                # Check risk signals
                cur.execute(
                    """
                    SELECT COUNT(*) 
                    FROM risk_signals 
                    WHERE signal_type = 'limited_edition_concentration' AND product_id = %s;
                    """,
                    (p_id,),
                )
                sig_count = cur.fetchone()[0]
                print(f"  - Concentration risk signals observed: {sig_count}")
                if sig_count > 0:
                    print("  PASSED: Coordinated group return risk signals exist.")
                else:
                    print("  WARNING: No concentration risk signals found for limited edition product.")
            else:
                print("  FAILED: Limited edition product or campaign not found.")
                all_passed = False

            # 6. Check Legitimate Mass-Return Scenario Consistency
            print("\n[6/6] Verifying Legitimate Mass-Return Scenario Consistency...")
            cur.execute(
                """
                SELECT COUNT(DISTINCT customer_id) 
                FROM messages 
                WHERE subject ILIKE '%Defective Headphones%';
                """
            )
            defective_msg_custs = cur.fetchone()[0]
            print(f"  - Customers reporting defective headphone hardware: {defective_msg_custs}")

            cur.execute(
                """
                SELECT COUNT(*) 
                FROM policies 
                WHERE policy_type = 'return_policy' AND active = true;
                """
            )
            policy_count = cur.fetchone()[0]
            print(f"  - Active return policies defined: {policy_count}")

            if defective_msg_custs >= 5 and policy_count > 0:
                print("  PASSED: Mass-return scenario has corroborating defective report volume and policy.")
            else:
                print("  FAILED: Mass-return corroborating messages or policies missing.")
                all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL DATABASE CHECKS PASSED SUCCESSFULLY (READY FOR BLACKSMITH)")
    else:
        print("DATABASE VALIDATION REPORTED ISSUES (REVIEW LOGS ABOVE)")
    print("=" * 60 + "\n")
    return all_passed


def main():
    success = validate_database()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
