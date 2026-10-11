"""Deterministic and idempotent database seeder for BlackSmith.

Preserves existing benchmark records and populates canonical support tickets
corresponding to the 10 benchmark test scenarios defined in Section 21.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import get_config
from database.connection import get_connection, get_transaction, initialize_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


BENCHMARK_SCENARIOS = [
    {
        "ticket_id": 1,
        "customer_id": 205,
        "order_id": 689,
        "subject": "Where is my package? Order tracking not updating",
        "body": "I ordered earbuds five days ago, but my package still hasn't arrived. Your tracking link hasn't updated in three days. Can you please check where my package is?",
        "category": "DELIVERY_STATUS",
        "sentiment": "NEGATIVE",
        "priority": "MEDIUM",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 2,
        "customer_id": 41,
        "order_id": None,
        "subject": "Defective Headphones - Audio Cutting Out",
        "body": "Hello, my Pro Noise-Cancelling Headphones keep cutting out Bluetooth connection every 2 minutes. I would like to request a replacement or refund for this defective unit.",
        "category": "DAMAGED_DEFECTIVE",
        "sentiment": "NEGATIVE",
        "priority": "HIGH",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 3,
        "customer_id": 218,
        "order_id": 749,
        "subject": "Return request - unwanted gift",
        "body": "Hi, I received this item as a gift but already have a similar pair. It is unopened in original packaging. I would like to return it for a refund.",
        "category": "REFUND_REQUEST",
        "sentiment": "NEUTRAL",
        "priority": "LOW",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 4,
        "customer_id": 96,
        "order_id": 623,
        "subject": "Refund request for summer order",
        "body": "I bought these headphones back in May and decided I do not use them enough. Please issue a full refund to my card.",
        "category": "REFUND_REQUEST",
        "sentiment": "NEUTRAL",
        "priority": "LOW",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 5,
        "customer_id": 153,
        "order_id": 462,
        "subject": "Unacceptable service and damaged product! Demanding manager",
        "body": "This is completely unacceptable! The product arrived completely smashed and your support team has ignored my previous emails. I want an immediate full refund and an explanation from a supervisor right now or I am disputing the charge!",
        "category": "COMPLAINT",
        "sentiment": "ANGRY",
        "priority": "URGENT",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 6,
        "customer_id": None,
        "order_id": None,
        "subject": "Need refund for broken charger",
        "body": "Hello, my charger stopped working yesterday. I want my money back. Please send me the refund as soon as possible.",
        "category": "REFUND_REQUEST",
        "sentiment": "NEGATIVE",
        "priority": "MEDIUM",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 7,
        "customer_id": 1,
        "order_id": 245,
        "subject": "Refund Status for Earbuds - Limited Edition Return",
        "body": "I purchased 5 units of the Limited Edition Wireless Earbuds during the launch campaign. I want to return all of them immediately for a full refund of $999.95.",
        "category": "REFUND_REQUEST",
        "sentiment": "NEUTRAL",
        "priority": "HIGH",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 8,
        "customer_id": 42,
        "order_id": None,
        "subject": "Pro Noise-Cancelling Headphones - Audio Dropout Defect",
        "body": "My headphones suffer from the known batch audio dropout defect when ANC is enabled. Requesting standard defective return under the 90-day hardware warranty.",
        "category": "DAMAGED_DEFECTIVE",
        "sentiment": "NEGATIVE",
        "priority": "HIGH",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 9,
        "customer_id": 188,
        "order_id": 527,
        "subject": "Question about international return shipping",
        "body": "Hello, I am currently traveling abroad and wanted to know if return shipping labels work internationally or if there is an extended window for travel?",
        "category": "PRODUCT_INQUIRY",
        "sentiment": "NEUTRAL",
        "priority": "LOW",
        "workflow_status": "NEW",
    },
    {
        "ticket_id": 10,
        "customer_id": 188,
        "order_id": 527,
        "subject": "High Value Bulk Return Request - Multiple Audio Stations",
        "body": "We are requesting a full return and refund of $1,019.97 for our recent multi-station order #527. Please execute the credit immediately.",
        "category": "REFUND_REQUEST",
        "sentiment": "NEUTRAL",
        "priority": "URGENT",
        "workflow_status": "NEW",
    },
]


def seed_database(force: bool = False) -> None:
    """Idempotently seed support tickets and scenario markers into PostgreSQL."""
    config = get_config()
    if not config.allow_seed and not force:
        logger.info(
            "Seeding skipped: ALLOW_SEED is false in configuration. "
            "Pass force=True or set ALLOW_SEED=true to seed."
        )
        return

    initialize_schema()

    with get_connection() as conn:
        with get_transaction(conn) as cur:
            # Check customer table count
            cur.execute("SELECT COUNT(*) FROM customers;")
            customer_count = cur.fetchone()[0]
            logger.info("Current customer count in database: %d", customer_count)

            # Insert scenario tickets idempotently
            inserted = 0
            for scenario in BENCHMARK_SCENARIOS:
                cur.execute(
                    """
                    INSERT INTO support_tickets (
                        ticket_id, customer_id, order_id, subject, body,
                        category, sentiment, priority, workflow_status, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (ticket_id) DO UPDATE
                    SET subject = EXCLUDED.subject,
                        body = EXCLUDED.body,
                        category = EXCLUDED.category,
                        sentiment = EXCLUDED.sentiment,
                        priority = EXCLUDED.priority;
                    """,
                    (
                        scenario["ticket_id"],
                        scenario["customer_id"],
                        scenario["order_id"],
                        scenario["subject"],
                        scenario["body"],
                        scenario["category"],
                        scenario["sentiment"],
                        scenario["priority"],
                        scenario["workflow_status"],
                    ),
                )
                inserted += 1

            # Advance sequence to prevent collisions on new tickets
            cur.execute(
                """
                SELECT setval(
                    'support_tickets_ticket_id_seq',
                    GREATEST((SELECT COALESCE(MAX(ticket_id), 1) FROM support_tickets), 10)
                );
                """
            )

            logger.info("Successfully seeded / synchronized %d benchmark support tickets.", inserted)


def main():
    parser = argparse.ArgumentParser(description="Seed BlackSmith database with benchmark scenario tickets.")
    parser.add_argument("--force", action="store_true", help="Force seeding regardless of ALLOW_SEED setting")
    args = parser.parse_args()

    seed_database(force=args.force)


if __name__ == "__main__":
    main()
