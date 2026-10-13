"""Support agent factory for BlackSmith (§9.1).

Provides one clear construction function that:
1. Initializes the Groq LLM with tool calling capability.
2. Validates that the model supports tool calling.
3. Binds the registered tool schemas to the LLM for proposal (NOT execution).
4. Returns a SupportAgent instance ready for the LangGraph workflow.

The agent PROPOSES tool calls. It does NOT execute them.
Execution passes through the governed_execute security boundary exclusively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

from core.config import AppConfig, get_config
from core.models import TicketCategory, TicketSentiment

logger = logging.getLogger(__name__)


@dataclass
class SupportAgent:
    """Configured LLM agent ready for ticket triage and response drafting.

    Attributes:
        llm: Initialized LangChain ChatGroq instance.
        llm_with_tools: LLM instance with tool schemas bound for proposal.
        model_name: Name of the active model.
        tool_names: List of registered tool names bound to this agent.
    """
    llm: Any
    llm_with_tools: Any
    model_name: str
    tool_names: List[str] = field(default_factory=list)

    def get_system_prompt(
        self,
        category: str = "UNKNOWN",
        sentiment: str = "NEUTRAL",
    ) -> str:
        """Return persona-appropriate system prompt for this ticket."""
        from agents.personas import get_system_prompt
        return get_system_prompt(category=category, sentiment=sentiment)


def build_support_agent(
    config: Optional[AppConfig] = None,
    populate_tools: bool = True,
) -> SupportAgent:
    """Factory function that constructs and validates the support agent.

    Args:
        config: Application configuration. Loads from environment if None.
        populate_tools: Whether to bind registered business tools to the LLM.

    Returns:
        Fully initialized SupportAgent.

    Raises:
        ImportError: If langchain_groq is not installed.
        ValueError: If configuration is invalid or model does not support tool calling.
        RuntimeError: If the LLM cannot be initialized.
    """
    try:
        from langchain_groq import ChatGroq
    except ImportError as e:
        raise ImportError(
            "langchain-groq is required. Install it via: pip install langchain-groq"
        ) from e

    cfg = config or get_config()

    logger.info("Initializing support agent with model: %s", cfg.groq_model)

    # Initialize LLM
    try:
        llm = ChatGroq(
            model=cfg.groq_model,
            api_key=cfg.groq_api_key,
            temperature=0,  # deterministic for support tasks
            max_retries=2,
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialize ChatGroq with model '{cfg.groq_model}': {e}\n"
            "Ensure GROQ_API_KEY is valid and the model name is correct."
        ) from e

    # Gather tool schemas for LLM binding
    lc_tools: List[Any] = []
    tool_names: List[str] = []

    if populate_tools:
        try:
            from customer_support.registry import get_registry
            reg = get_registry(populate_defaults=True)
            lc_tools = reg.to_langchain_tools()
            tool_names = [t.name for t in reg.list_tools()]
            logger.info("Bound %d tools to support agent.", len(lc_tools))
        except Exception as e:
            logger.error("Failed to load tools for agent binding: %s", e)
            raise RuntimeError(
                f"Tool registry initialization failed: {e}"
            ) from e

    # Bind tools to LLM for proposal (execution stays with gatekeeper)
    llm_with_tools = llm
    if lc_tools:
        try:
            llm_with_tools = llm.bind_tools(lc_tools)
            logger.debug("Successfully bound %d tools to LLM.", len(lc_tools))
        except Exception as e:
            logger.warning(
                "Could not bind tools to LLM (%s); agent will operate without tool proposals.", e
            )
            llm_with_tools = llm

    return SupportAgent(
        llm=llm,
        llm_with_tools=llm_with_tools,
        model_name=cfg.groq_model,
        tool_names=tool_names,
    )
