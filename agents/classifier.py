"""Ticket classification using structured LLM output (§9.2).

Provides deterministic, validated classification of support tickets with:
- Category (TicketCategory enum)
- Sentiment (TicketSentiment enum)
- Priority (TicketPriority enum)
- Confidence score
- Reasoning explanation
- Detected entity identifiers (customer_id, order_id where extractable)

Uses LLM structured output via Pydantic schema where supported.
Falls back to JSON parsing if structured_output is unavailable.
Provides safe defaults if classification entirely fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from core.models import TicketCategory, TicketClassification, TicketPriority, TicketSentiment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema for structured LLM classification
# ---------------------------------------------------------------------------

class TicketClassificationResult(BaseModel):
    """Structured output schema for LLM ticket classification."""

    category: str = Field(
        description="One of: DELIVERY_STATUS, DAMAGED_DEFECTIVE, REFUND_REQUEST, "
                    "PRODUCT_INQUIRY, ACCOUNT_ACCESS, COMPLAINT, UNKNOWN"
    )
    sentiment: str = Field(
        description="One of: POSITIVE, NEUTRAL, NEGATIVE, ANGRY"
    )
    priority: str = Field(
        description="One of: LOW, MEDIUM, HIGH, URGENT"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.8,
        description="Confidence score between 0.0 and 1.0"
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of the classification decision"
    )
    detected_customer_id: Optional[int] = Field(
        default=None,
        description="Customer ID if explicitly present in the ticket text"
    )
    detected_order_id: Optional[int] = Field(
        default=None,
        description="Order ID if explicitly present in the ticket text"
    )

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        valid = {e.value for e in TicketCategory}
        upper = v.strip().upper()
        if upper in valid:
            return upper
        return TicketCategory.UNKNOWN.value

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        valid = {e.value for e in TicketSentiment}
        upper = v.strip().upper()
        if upper in valid:
            return upper
        return TicketSentiment.NEUTRAL.value

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        valid = {e.value for e in TicketPriority}
        upper = v.strip().upper()
        if upper in valid:
            return upper
        return TicketPriority.MEDIUM.value

    def to_ticket_classification(self) -> TicketClassification:
        """Convert to canonical TicketClassification domain model."""
        return TicketClassification(
            category=TicketCategory(self.category),
            sentiment=TicketSentiment(self.sentiment),
            priority=TicketPriority(self.priority),
            confidence=self.confidence,
            reasoning=self.reasoning,
            detected_entities={
                k: v for k, v in {
                    "customer_id": self.detected_customer_id,
                    "order_id": self.detected_order_id,
                }.items() if v is not None
            },
        )


# ---------------------------------------------------------------------------
# Fallback defaults for classification failures
# ---------------------------------------------------------------------------

_FALLBACK_CLASSIFICATION = TicketClassification(
    category=TicketCategory.UNKNOWN,
    sentiment=TicketSentiment.NEUTRAL,
    priority=TicketPriority.MEDIUM,
    confidence=0.0,
    reasoning="Classification failed; safe default applied.",
    detected_entities={},
)

_CLASSIFICATION_SYSTEM_PROMPT = """\
You are a customer support ticket classification system. 
Analyze the provided ticket text and classify it with these exact fields:

- category: DELIVERY_STATUS | DAMAGED_DEFECTIVE | REFUND_REQUEST | PRODUCT_INQUIRY | ACCOUNT_ACCESS | COMPLAINT | UNKNOWN
- sentiment: POSITIVE | NEUTRAL | NEGATIVE | ANGRY
- priority: LOW | MEDIUM | HIGH | URGENT
- confidence: float 0.0-1.0
- reasoning: 1-2 sentence explanation
- detected_customer_id: integer if mentioned in text, otherwise null
- detected_order_id: integer if mentioned in text, otherwise null

Priority guidance:
- URGENT: financial impact, repeated failure, safety issue, account blocked
- HIGH: unresolved complaint, frustrated/angry customer, refund blocked
- MEDIUM: routine inquiry, minor frustration, standard request
- LOW: general question, positive feedback, informational

Respond ONLY with a valid JSON object matching these exact fields.
Do not include any explanation outside the JSON.
"""


def _parse_classification_from_text(text: str) -> Optional[TicketClassificationResult]:
    """Attempt to extract and parse a JSON classification from raw LLM text output."""
    # Try to find JSON block
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
        return TicketClassificationResult.model_validate(data)
    except Exception as e:
        logger.warning("Failed to parse classification JSON: %s | text: %s", e, text[:200])
        return None


def classify_ticket(
    ticket_text: str,
    llm: Any,
) -> TicketClassification:
    """Classify a support ticket using the provided LLM.

    Tries structured output first (with_structured_output), falls back to
    raw JSON parsing if the model or version doesn't support it.
    Returns a safe fallback classification on total failure.

    Args:
        ticket_text: Raw customer message or ticket body.
        llm: Initialized LangChain LLM instance (e.g. ChatGroq).

    Returns:
        TicketClassification domain model.
    """
    if not ticket_text or not ticket_text.strip():
        logger.warning("Empty ticket text passed to classifier; returning fallback.")
        return _FALLBACK_CLASSIFICATION

    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=_CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=f"Classify this customer ticket:\n\n{ticket_text.strip()}"),
    ]

    # Attempt 1: structured output via with_structured_output
    try:
        structured_llm = llm.with_structured_output(TicketClassificationResult)
        result: TicketClassificationResult = structured_llm.invoke(messages)
        logger.debug("Ticket classified via structured output: category=%s", result.category)
        return result.to_ticket_classification()
    except Exception as structured_err:
        logger.warning(
            "Structured output classification failed (%s); falling back to JSON parsing.",
            structured_err,
        )

    # Attempt 2: raw invoke and parse JSON from text
    try:
        raw_response = llm.invoke(messages)
        content = getattr(raw_response, "content", str(raw_response))
        parsed = _parse_classification_from_text(str(content))
        if parsed:
            logger.debug(
                "Ticket classified via JSON parse fallback: category=%s", parsed.category
            )
            return parsed.to_ticket_classification()
    except Exception as parse_err:
        logger.error("JSON fallback classification also failed: %s", parse_err)

    # Total failure → safe default
    logger.error("All classification attempts failed; using safe fallback classification.")
    return _FALLBACK_CLASSIFICATION
