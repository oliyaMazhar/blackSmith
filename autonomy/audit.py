"""Persistent audit logging for governance decisions and tool execution (§18).

Writes immutable audit records to the PostgreSQL audit_records table,
capturing run IDs, proposed arguments, decisions, risk scores, explanations,
and execution outcomes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from core.models import AuditEvent, ExecutionMode
from decimal import Decimal
from datetime import date, datetime
from database.connection import get_connection

logger = logging.getLogger(__name__)

# Sensitive keys to sanitize from audit records
REDACTED_KEYS = {"password", "secret", "token", "api_key", "authorization"}


def sanitize_dict(data: Any) -> Any:
    """Recursively redact sensitive keys and format Decimal/datetime before audit persistence."""
    if isinstance(data, Decimal):
        return float(data)
    elif isinstance(data, (datetime, date)):
        return data.isoformat()
    elif isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if any(red in str(k).lower() for red in REDACTED_KEYS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_dict(v)
        return sanitized
    elif isinstance(data, (list, tuple)):
        return [sanitize_dict(item) for item in data]
    return data


def record_audit_event(
    run_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    decision: ExecutionMode,
    risk_score: float,
    explanation: str,
    execution_result: Optional[Dict[str, Any]] = None,
) -> int:
    """Persist an authoritative governance audit record to PostgreSQL audit_records table."""
    sanitized_args = sanitize_dict(arguments)
    sanitized_result = sanitize_dict(execution_result) if execution_result is not None else None

    # PostgreSQL schema constraint: risk_score is NUMERIC(5,4), max is 9.9999, valid range is 0.0000 - 1.0000
    clamped_score = max(0.0000, min(1.0000, float(risk_score)))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_records (
                    run_id, tool_name, arguments, authorization_decision,
                    risk_score, explanation, execution_result, created_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                RETURNING audit_id;
                """,
                (
                    run_id,
                    tool_name,
                    json.dumps(sanitized_args, default=str),
                    decision.value,
                    clamped_score,
                    explanation,
                    json.dumps(sanitized_result, default=str) if sanitized_result is not None else None,
                ),
            )
            audit_id = cur.fetchone()[0]
        conn.commit()

    logger.debug("Persisted audit record #%s for tool '%s' (decision=%s)", audit_id, tool_name, decision.value)
    return audit_id


def get_audit_records(
    run_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Retrieve historical audit records from PostgreSQL."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT audit_id, run_id, tool_name, arguments, authorization_decision,
                       risk_score, explanation, execution_result, created_at
                FROM audit_records
                WHERE (%s IS NULL OR run_id = %s)
                  AND (%s IS NULL OR tool_name = %s)
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (run_id, run_id, tool_name, tool_name, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "audit_id": r[0],
                    "run_id": r[1],
                    "tool_name": r[2],
                    "arguments": r[3],
                    "authorization_decision": r[4],
                    "risk_score": float(r[5]),
                    "explanation": r[6],
                    "execution_result": r[7],
                    "created_at": r[8].isoformat() if r[8] else None,
                }
                for r in rows
            ]
