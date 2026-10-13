"""Unit tests for Centralized Tool Registry (§23.1).

Verifies:
1. Registered tools can be resolved by canonical name.
2. Duplicate tool names are strictly rejected.
3. Unknown tools cannot execute.
4. Invalid arguments are rejected before invoking underlying function.
5. Successful tool calls return actual business results.
6. Execution timing and error safety.
7. LangChain StructuredTool conversion and schema export.
"""

from __future__ import annotations

from typing import Any, Dict
import pytest
from pydantic import BaseModel, Field, ValidationError

from core.models import ActionCategory, RiskLevel, ToolDefinition, ToolExecutionResult
from customer_support.registry import (
    DuplicateToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolValidationError,
)


class DummyInput(BaseModel):
    """Test argument schema."""
    user_id: int = Field(..., description="User ID")
    note: str = Field("default_note", min_length=2, description="Optional note")


def dummy_func(user_id: int, note: str = "default_note") -> Dict[str, Any]:
    """Test callable returning structured business result."""
    return {"user_id": user_id, "note": note, "processed": True}


def failing_func(user_id: int, note: str = "default_note") -> None:
    """Test callable raising runtime error."""
    raise RuntimeError("Underlying business logic exploded.")


@pytest.fixture
def empty_registry() -> ToolRegistry:
    """Create an isolated, empty ToolRegistry instance."""
    return ToolRegistry()


def test_tool_registration_and_resolution(empty_registry):
    """Tool can be registered and resolved by canonical name."""
    tool_def = ToolDefinition(
        name="test.dummy",
        description="A dummy test tool.",
        arg_schema=DummyInput,
        function=dummy_func,
        permissions=["test.read"],
        risk_level=RiskLevel.LOW,
        is_mutation=False,
    )
    empty_registry.register(tool_def)

    assert empty_registry.has_tool("test.dummy") is True
    assert empty_registry.has_tool("nonexistent.tool") is False

    resolved = empty_registry.get_tool("test.dummy")
    assert resolved.name == "test.dummy"
    assert resolved.description == "A dummy test tool."
    assert resolved.permissions == ["test.read"]

    # Resolution via get() returns definition
    assert empty_registry.get("test.dummy") is not None
    assert empty_registry.get("nonexistent") is None

    # Listing
    tools = empty_registry.list_tools()
    assert len(tools) == 1
    assert empty_registry.list_tool_names() == ["test.dummy"]


def test_duplicate_tool_name_rejected(empty_registry):
    """Attempting to register a tool with an existing name raises DuplicateToolError."""
    tool1 = ToolDefinition(
        name="test.duplicate",
        description="First definition.",
        arg_schema=DummyInput,
        function=dummy_func,
    )
    tool2 = ToolDefinition(
        name="test.duplicate",
        description="Second duplicate definition.",
        arg_schema=DummyInput,
        function=dummy_func,
    )
    empty_registry.register(tool1)

    with pytest.raises(DuplicateToolError) as exc_info:
        empty_registry.register(tool2)

    assert "is already registered" in str(exc_info.value)


def test_invalid_tool_definitions_rejected(empty_registry):
    """Invalid names, descriptions, or non-callable functions are rejected."""
    # Empty name
    with pytest.raises(ValueError):
        empty_registry.register(
            ToolDefinition(
                name="",
                description="desc",
                arg_schema=DummyInput,
                function=dummy_func,
            )
        )

    # Empty description
    with pytest.raises(ValueError):
        empty_registry.register(
            ToolDefinition(
                name="test.nodesc",
                description="  ",
                arg_schema=DummyInput,
                function=dummy_func,
            )
        )

    # Non-callable function
    with pytest.raises(TypeError):
        empty_registry.register(
            ToolDefinition(
                name="test.notcallable",
                description="desc",
                arg_schema=DummyInput,
                function=None,  # type: ignore
            )
        )


def test_unknown_tool_cannot_execute(empty_registry):
    """Unknown tools cannot execute and return clean error result or raise ToolNotFoundError."""
    # Without raise_on_error
    result = empty_registry.execute("ghost.tool", {"user_id": 1})
    assert result.success is False
    assert "is not registered" in result.error
    assert result.tool_name == "ghost.tool"

    # With raise_on_error
    with pytest.raises(ToolNotFoundError):
        empty_registry.execute("ghost.tool", {"user_id": 1}, raise_on_error=True)


def test_invalid_arguments_rejected_before_execution(empty_registry):
    """Missing required parameters or type mismatches are rejected before execution."""
    empty_registry.register(
        ToolDefinition(
            name="test.argcheck",
            description="Checks argument validation.",
            arg_schema=DummyInput,
            function=dummy_func,
        )
    )

    # Missing required 'user_id'
    res_missing = empty_registry.execute("test.argcheck", {"note": "missing user id"})
    assert res_missing.success is False
    assert "Argument validation failed" in res_missing.error

    # Note too short (min_length=2)
    res_short = empty_registry.execute("test.argcheck", {"user_id": 1, "note": "x"})
    assert res_short.success is False
    assert "Argument validation failed" in res_short.error

    # Invalid type for user_id
    res_type = empty_registry.execute("test.argcheck", {"user_id": "not-an-integer", "note": "ok"})
    assert res_type.success is False
    assert "Argument validation failed" in res_type.error

    # With raise_on_error
    with pytest.raises(ToolValidationError):
        empty_registry.execute("test.argcheck", {"note": "missing"}, raise_on_error=True)


def test_successful_tool_call_returns_business_result(empty_registry):
    """Successful tool execution returns actual output and execution telemetry."""
    empty_registry.register(
        ToolDefinition(
            name="test.success",
            description="Returns business result.",
            arg_schema=DummyInput,
            function=dummy_func,
            is_mutation=True,
            action_category=ActionCategory.FINANCIAL_MUTATION,
        )
    )

    result = empty_registry.execute("test.success", {"user_id": 42, "note": "hello world"})
    assert result.success is True
    assert result.error is None
    assert result.tool_name == "test.success"
    assert result.mutation_performed is True
    assert result.execution_time_ms >= 0.0

    # Verify actual business result structure
    assert result.output == {"user_id": 42, "note": "hello world", "processed": True}


def test_runtime_execution_failure_handled_safely(empty_registry):
    """Underlying exceptions are trapped and returned as failure results."""
    empty_registry.register(
        ToolDefinition(
            name="test.failing",
            description="Raises runtime error.",
            arg_schema=DummyInput,
            function=failing_func,
        )
    )

    # Without raise_on_error
    result = empty_registry.execute("test.failing", {"user_id": 99})
    assert result.success is False
    assert "Underlying business logic exploded." in result.error
    assert result.mutation_performed is False

    # With raise_on_error
    with pytest.raises(ToolExecutionError):
        empty_registry.execute("test.failing", {"user_id": 99}, raise_on_error=True)


def test_langchain_structured_tools_conversion(empty_registry):
    """Tools in registry convert cleanly to LangChain StructuredTools."""
    empty_registry.register(
        ToolDefinition(
            name="order.read",
            description="Read order details.",
            arg_schema=DummyInput,
            function=dummy_func,
        )
    )

    lc_tools = empty_registry.to_langchain_tools()
    assert len(lc_tools) == 1
    tool = lc_tools[0]

    # Name is sanitized for LLM provider compat
    assert tool.name == "order_read"
    assert tool.description == "Read order details."
    assert tool.args_schema == DummyInput

    # Execution via LangChain invocation
    result = tool.invoke({"user_id": 101, "note": "lc_call"})
    assert result == {"user_id": 101, "note": "lc_call", "processed": True}


def test_registry_schemas_export(empty_registry):
    """Registry exports standard JSON schemas for LLM inspection."""
    empty_registry.register(
        ToolDefinition(
            name="test.schema",
            description="Schema export test.",
            arg_schema=DummyInput,
            function=dummy_func,
            permissions=["perm.read"],
            risk_level=RiskLevel.MEDIUM,
            is_mutation=True,
            action_category=ActionCategory.INVENTORY_MUTATION,
        )
    )

    schemas = empty_registry.get_schemas()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["name"] == "test.schema"
    assert s["description"] == "Schema export test."
    assert s["permissions"] == ["perm.read"]
    assert s["risk_level"] == "MEDIUM"
    assert s["is_mutation"] is True
    assert s["action_category"] == "INVENTORY_MUTATION"
    assert "properties" in s["parameters"]
    assert "user_id" in s["parameters"]["properties"]
