"""Agent construction and configuration module for BlackSmith.

Contains:
- support_agent: Factory for creating the LLM-powered customer support agent.
- classifier: Structured ticket classification using the LLM.
- personas: System prompt templates for different support roles.
"""

from agents.support_agent import build_support_agent, SupportAgent
from agents.classifier import classify_ticket, TicketClassificationResult

__all__ = [
    "build_support_agent",
    "SupportAgent",
    "classify_ticket",
    "TicketClassificationResult",
]
