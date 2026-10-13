"""Centralized canonical tool registry for BlackSmith.

Provides the authoritative inventory of executable business capabilities:
- Registers tools with explicit permission requirements, inherent risk classification,
  and validated argument schemas.
- Rejects duplicate tool registrations and invalid definitions.
- Validates all invocation arguments against Pydantic schemas before execution.
- Rejects unregistered tool executions and arbitrary code invocation.
- Integrates with LangChain/LangGraph structured tools.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Type
from pydantic import BaseModel, ValidationError

from core.models import (
    ActionCategory,
    RiskLevel,
    ToolDefinition,
    ToolExecutionResult,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Exceptions
# ============================================================================

class ToolRegistryError(Exception):
    """Base error for tool registry operations."""
    pass


class DuplicateToolError(ToolRegistryError):
    """Raised when registering a tool whose name is already present."""
    pass


class ToolNotFoundError(ToolRegistryError):
    """Raised when attempting to access or execute an unregistered tool."""
    pass


class ToolValidationError(ToolRegistryError):
    """Raised when tool arguments fail Pydantic schema validation."""
    pass


class ToolExecutionError(ToolRegistryError):
    """Raised when an underlying tool callable raises an unhandled exception."""
    pass


# ============================================================================
# Tool Registry Implementation
# ============================================================================

class ToolRegistry:
    """Canonical registry holding all validated business tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """Register a new tool definition.
        
        Validates completeness and rejects duplicates.
        """
        if not tool.name or not tool.name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        canonical_name = tool.name.strip()
        if canonical_name in self._tools:
            raise DuplicateToolError(
                f"Tool '{canonical_name}' is already registered in the registry. "
                f"Duplicate registrations are strictly prohibited."
            )

        if not tool.description or not tool.description.strip():
            raise ValueError(f"Tool '{canonical_name}' must provide a descriptive explanation.")

        if not issubclass(tool.arg_schema, BaseModel):
            raise TypeError(f"Tool '{canonical_name}' arg_schema must be a Pydantic BaseModel subclass.")

        if not callable(tool.function):
            raise TypeError(f"Tool '{canonical_name}' function must be a callable execution target.")

        self._tools[canonical_name] = tool
        logger.debug("Registered tool '%s' (risk=%s, mutation=%s)", canonical_name, tool.risk_level, tool.is_mutation)
        return tool

    def register_tool(
        self,
        name: str,
        description: str,
        arg_schema: Type[BaseModel],
        function: Callable[..., Any],
        permissions: Optional[List[str]] = None,
        risk_level: RiskLevel = RiskLevel.LOW,
        is_mutation: bool = False,
        side_effect: bool = False,
        reversible: bool = False,
        action_category: ActionCategory = ActionCategory.READ,
    ) -> ToolDefinition:
        """Helper to construct and register a ToolDefinition in one step."""
        tool = ToolDefinition(
            name=name,
            description=description,
            arg_schema=arg_schema,
            function=function,
            permissions=permissions or [],
            risk_level=risk_level,
            is_mutation=is_mutation,
            side_effect=side_effect,
            reversible=reversible,
            action_category=action_category,
        )
        return self.register(tool)

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Retrieve a tool definition by name or None if not registered."""
        return self._tools.get(name.strip())

    def get_tool(self, name: str) -> ToolDefinition:
        """Retrieve a tool definition by name, raising ToolNotFoundError if missing."""
        canonical_name = name.strip()
        if canonical_name not in self._tools:
            raise ToolNotFoundError(
                f"Tool '{canonical_name}' is not registered in the tool registry. "
                f"Available tools: {', '.join(sorted(self._tools.keys())) or 'none'}"
            )
        return self._tools[canonical_name]

    def has_tool(self, name: str) -> bool:
        """Check if a tool exists in the registry."""
        return name.strip() in self._tools

    def list_tools(self) -> List[ToolDefinition]:
        """Return all registered tool definitions in deterministic alphabetical order."""
        return [self._tools[k] for k in sorted(self._tools.keys())]

    def list_tool_names(self) -> List[str]:
        """Return all registered tool names sorted."""
        return sorted(self._tools.keys())

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry. Primarily used in isolated test setups."""
        canonical_name = name.strip()
        if canonical_name in self._tools:
            del self._tools[canonical_name]
            return True
        return False

    def clear(self) -> None:
        """Clear all registered tools."""
        self._tools.clear()

    def execute(
        self,
        name: str,
        arguments: Dict[str, Any] | BaseModel,
        raise_on_error: bool = False,
    ) -> ToolExecutionResult:
        """Execute a registered business tool with strict argument validation.
        
        Steps:
        1. Resolve tool by name. Reject if unregistered.
        2. Validate arguments against tool's Pydantic arg_schema.
        3. Execute callable with validated arguments and measure runtime.
        4. Return standardized ToolExecutionResult.
        """
        canonical_name = name.strip()
        if canonical_name not in self._tools:
            err_msg = (
                f"Tool '{canonical_name}' is not registered. Cannot execute unregistered tools."
            )
            if raise_on_error:
                raise ToolNotFoundError(err_msg)
            return ToolExecutionResult(
                success=False,
                error=err_msg,
                tool_name=canonical_name,
                mutation_performed=False,
            )

        tool = self._tools[canonical_name]

        # 2. Argument validation
        try:
            if isinstance(arguments, tool.arg_schema):
                validated_args = arguments
            elif isinstance(arguments, BaseModel):
                validated_args = tool.arg_schema.model_validate(arguments.model_dump())
            elif isinstance(arguments, dict):
                validated_args = tool.arg_schema.model_validate(arguments)
            else:
                raise TypeError(
                    f"Arguments for tool '{canonical_name}' must be dict or BaseModel, "
                    f"got {type(arguments).__name__}."
                )
        except (ValidationError, TypeError, ValueError) as val_err:
            err_msg = f"Argument validation failed for tool '{canonical_name}': {val_err}"
            if raise_on_error:
                raise ToolValidationError(err_msg) from val_err
            return ToolExecutionResult(
                success=False,
                error=err_msg,
                tool_name=canonical_name,
                mutation_performed=False,
            )

        # 3. Invocation
        start_time = time.perf_counter()
        try:
            kwargs = validated_args.model_dump()
            result = tool.function(**kwargs)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return ToolExecutionResult(
                success=True,
                output=result,
                tool_name=canonical_name,
                execution_time_ms=round(duration_ms, 2),
                mutation_performed=tool.is_mutation,
            )
        except Exception as exec_err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Execution failed for tool '%s': %s", canonical_name, exec_err, exc_info=True)
            if raise_on_error:
                raise ToolExecutionError(f"Tool '{canonical_name}' failed during execution: {exec_err}") from exec_err
            return ToolExecutionResult(
                success=False,
                error=str(exec_err),
                tool_name=canonical_name,
                execution_time_ms=round(duration_ms, 2),
                mutation_performed=False,
            )

    def to_langchain_tools(self) -> List[Any]:
        """Convert all registered tools into LangChain StructuredTools.
        
        Permits direct integration with ChatGroq, ChatOpenAI, and LangGraph tool execution nodes.
        Names are formatted with underscores for universal LLM provider compatibility.
        """
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as e:
            raise ImportError(
                "langchain-core is required to generate LangChain StructuredTools. "
                "Ensure requirements.txt is installed."
            ) from e

        lc_tools = []
        for tool in self.list_tools():
            clean_name = tool.name.replace(".", "_")

            # Create closure for current tool execution
            def make_executor(t_name: str):
                def _executor(**kwargs: Any) -> Any:
                    exec_result = self.execute(t_name, kwargs, raise_on_error=True)
                    return exec_result.output
                return _executor

            st = StructuredTool.from_function(
                func=make_executor(tool.name),
                name=clean_name,
                description=tool.description,
                args_schema=tool.arg_schema,
            )
            lc_tools.append(st)

        return lc_tools

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Return standardized JSON schemas of all registered tools."""
        schemas = []
        for tool in self.list_tools():
            schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.arg_schema.model_json_schema(),
                    "permissions": tool.permissions,
                    "risk_level": tool.risk_level.value,
                    "is_mutation": tool.is_mutation,
                    "side_effect": tool.side_effect,
                    "reversible": tool.reversible,
                    "action_category": tool.action_category.value,
                }
            )
        return schemas


# ============================================================================
# Canonical Global Registry Instance
# ============================================================================

_GLOBAL_REGISTRY: Optional[ToolRegistry] = None


def get_registry(populate_defaults: bool = True) -> ToolRegistry:
    """Retrieve the global canonical tool registry, initializing and populating if necessary."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ToolRegistry()
        if populate_defaults:
            from customer_support.tools import register_all_tools
            register_all_tools(_GLOBAL_REGISTRY)
    elif populate_defaults and len(_GLOBAL_REGISTRY.list_tools()) == 0:
        from customer_support.tools import register_all_tools
        register_all_tools(_GLOBAL_REGISTRY)
    return _GLOBAL_REGISTRY


def reset_registry(populate_defaults: bool = False) -> ToolRegistry:
    """Reset the global registry instance (useful in isolated tests)."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = ToolRegistry()
    if populate_defaults:
        from customer_support.tools import register_all_tools
        register_all_tools(_GLOBAL_REGISTRY)
    return _GLOBAL_REGISTRY


# Canonical alias exported for module consumers
registry = get_registry(populate_defaults=True)
