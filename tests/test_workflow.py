"""Tests for the LangGraph workflow: Stage 5 nodes and routing (§23.6).

These tests use controlled mock agents and mock tool execution to:
- Verify ticket intake saves a record and populates state.
- Verify classification parses and stores structured results.
- Verify agent reasoning routes to tool execution or drafting.
- Verify execute_tools node ALWAYS passes through governed_execute.
- Verify blocked mutations remain blocked.
- Verify the graph terminates within configured iteration limits.
- Verify draft_response produces a non-empty draft.
- Verify final ticket status and draft are persisted correctly.

Database-dependent tests are skipped if DATABASE_URL is unavailable.
LLM-dependent tests use mock LLMs (no live Groq API required).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers and mocks
# ---------------------------------------------------------------------------

def _make_run_id() -> str:
    return f"test-run-{uuid.uuid4().hex[:8]}"


from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class MockAIMessage(AIMessage):
    """Mock for langchain_core.messages.AIMessage."""
    def __init__(self, content: str = "", tool_calls: Optional[List] = None, **kwargs):
        super().__init__(content=content, tool_calls=tool_calls or [], **kwargs)


class MockLLM:
    """Deterministic mock LLM that always returns a fixed response."""

    def __init__(
        self,
        response_content: str = "Thank you for contacting us.",
        tool_calls: Optional[List] = None,
    ):
        self._response = MockAIMessage(
            content=response_content,
            tool_calls=tool_calls or [],
        )

    def invoke(self, messages) -> MockAIMessage:
        return self._response

    def bind_tools(self, tools) -> "MockLLM":
        return self

    def with_structured_output(self, schema) -> "MockStructuredLLM":
        return MockStructuredLLM(schema)


class MockStructuredLLM:
    """Mock for with_structured_output() that raises to force fallback."""

    def __init__(self, schema):
        self._schema = schema

    def invoke(self, messages):
        # Return a valid classification-like dict
        if hasattr(self._schema, "model_validate"):
            return self._schema.model_validate({
                "category": "DELIVERY_STATUS",
                "sentiment": "NEGATIVE",
                "priority": "HIGH",
                "confidence": 0.9,
                "reasoning": "Customer mentions delayed package.",
                "detected_customer_id": None,
                "detected_order_id": None,
            })
        raise RuntimeError("Structured output not supported")


class MockSupportAgent:
    """Mock agent for workflow tests."""
    def __init__(self, response_content="Thank you for your complaint.", tool_calls=None):
        mock_llm = MockLLM(
            response_content=response_content,
            tool_calls=tool_calls,
        )
        self.llm = mock_llm
        self.llm_with_tools = mock_llm
        self.model_name = "mock-model"
        self.tool_names = ["get_customer_info", "get_order_details"]

    def get_system_prompt(self, category: str = "UNKNOWN", sentiment: str = "NEUTRAL") -> str:
        from agents.personas import get_system_prompt
        return get_system_prompt(category=category, sentiment=sentiment)


# ---------------------------------------------------------------------------
# State utilities
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> dict:
    from graph.state import make_initial_state
    state = make_initial_state(
        run_id=_make_run_id(),
        ticket_body="My package never arrived after 10 days.",
        ticket_subject="Missing package",
    )
    state.update(overrides)
    return state


# ===========================================================================
# Classifier tests (no LLM needed — mock-based)
# ===========================================================================

class TestClassifier:
    """Tests for agents.classifier module."""

    def test_ticket_classification_result_validates_valid_input(self):
        """Valid classification data passes Pydantic validation."""
        from agents.classifier import TicketClassificationResult
        result = TicketClassificationResult(
            category="REFUND_REQUEST",
            sentiment="ANGRY",
            priority="HIGH",
            confidence=0.95,
            reasoning="Customer demands a refund.",
            detected_customer_id=None,
            detected_order_id=42,
        )
        classification = result.to_ticket_classification()
        from core.models import TicketCategory, TicketSentiment, TicketPriority
        assert classification.category == TicketCategory.REFUND_REQUEST
        assert classification.sentiment == TicketSentiment.ANGRY
        assert classification.priority == TicketPriority.HIGH
        assert classification.detected_entities.get("order_id") == 42

    def test_ticket_classification_result_coerces_invalid_category(self):
        """Invalid category defaults to UNKNOWN."""
        from agents.classifier import TicketClassificationResult
        result = TicketClassificationResult(
            category="NONSENSE_CATEGORY",
            sentiment="NEUTRAL",
            priority="MEDIUM",
        )
        assert result.category == "UNKNOWN"

    def test_classify_ticket_with_mock_structured_llm(self):
        """classify_ticket returns TicketClassification using mock LLM."""
        from agents.classifier import classify_ticket
        agent = MockSupportAgent()
        result = classify_ticket("My package is late.", agent.llm)
        assert result is not None
        assert result.category is not None
        assert result.confidence >= 0.0

    def test_classify_ticket_empty_body_returns_fallback(self):
        """Empty ticket body returns safe fallback without crashing."""
        from agents.classifier import classify_ticket
        from core.models import TicketCategory
        agent = MockSupportAgent()
        result = classify_ticket("", agent.llm)
        assert result.category == TicketCategory.UNKNOWN
        assert result.confidence == 0.0

    def test_classify_ticket_llm_failure_returns_fallback(self):
        """LLM that raises returns the safe fallback classification."""
        from agents.classifier import classify_ticket
        from core.models import TicketCategory

        class FailingLLM:
            def with_structured_output(self, schema):
                return self
            def invoke(self, messages):
                raise RuntimeError("Model unavailable")

        result = classify_ticket("Complaint about order.", FailingLLM())
        assert result.category == TicketCategory.UNKNOWN


# ===========================================================================
# Persona tests
# ===========================================================================

class TestPersonas:
    """Tests for agents.personas system prompts."""

    def test_get_system_prompt_returns_base(self):
        """Default call returns base prompt."""
        from agents.personas import get_system_prompt, BASE_SYSTEM_PROMPT
        prompt = get_system_prompt()
        assert BASE_SYSTEM_PROMPT in prompt

    def test_angry_sentiment_includes_supplement(self):
        """ANGRY sentiment adds the angry customer supplement."""
        from agents.personas import get_system_prompt, ANGRY_CUSTOMER_SUPPLEMENT
        prompt = get_system_prompt(sentiment="ANGRY")
        assert ANGRY_CUSTOMER_SUPPLEMENT in prompt

    def test_refund_category_includes_supplement(self):
        """REFUND_REQUEST category adds the refund specialist supplement."""
        from agents.personas import get_system_prompt, REFUND_SPECIALIST_SUPPLEMENT
        prompt = get_system_prompt(category="REFUND_REQUEST")
        assert REFUND_SPECIALIST_SUPPLEMENT in prompt

    def test_delivery_category_includes_supplement(self):
        """DELIVERY_STATUS adds the delivery specialist supplement."""
        from agents.personas import get_system_prompt, DELIVERY_SPECIALIST_SUPPLEMENT
        prompt = get_system_prompt(category="DELIVERY_STATUS")
        assert DELIVERY_SPECIALIST_SUPPLEMENT in prompt

    def test_unknown_category_returns_only_base(self):
        """Unknown category doesn't add unexpected supplements."""
        from agents.personas import get_system_prompt, BASE_SYSTEM_PROMPT
        prompt = get_system_prompt(category="UNKNOWN", sentiment="NEUTRAL")
        assert BASE_SYSTEM_PROMPT in prompt
        # Should not include a broken partial supplement
        assert "SPECIAL HANDLING" not in prompt.replace(BASE_SYSTEM_PROMPT, "")


# ===========================================================================
# Routing tests (pure logic, no DB or LLM)
# ===========================================================================

class TestRouting:
    """Tests for graph routing functions."""

    def test_route_after_intake_failed(self):
        from graph.routing import route_after_intake
        state = _make_state(workflow_status="failed")
        assert route_after_intake(state) == "end_failed"

    def test_route_after_intake_success(self):
        from graph.routing import route_after_intake
        state = _make_state(workflow_status="classifying")
        assert route_after_intake(state) == "classify"

    def test_route_after_agent_reason_with_pending_tools(self):
        from graph.routing import route_after_agent_reason
        state = _make_state(
            workflow_status="executing",
            pending_tool_calls=[{"name": "get_order_details", "args": {"order_id": 1}}],
            tool_iteration_count=0,
        )
        assert route_after_agent_reason(state, max_iterations=8) == "execute_tools"

    def test_route_after_agent_reason_no_tools_goes_to_draft(self):
        from graph.routing import route_after_agent_reason
        state = _make_state(
            workflow_status="drafting",
            pending_tool_calls=[],
            tool_iteration_count=0,
        )
        assert route_after_agent_reason(state, max_iterations=8) == "draft_response"

    def test_route_after_agent_reason_max_iterations_forces_draft(self):
        from graph.routing import route_after_agent_reason
        state = _make_state(
            workflow_status="reasoning",
            pending_tool_calls=[{"name": "some_tool", "args": {}}],
            tool_iteration_count=8,
        )
        assert route_after_agent_reason(state, max_iterations=8) == "draft_response"

    def test_route_after_execute_tools_loops_back_to_reason(self):
        from graph.routing import route_after_execute_tools
        state = _make_state(
            workflow_status="reasoning",
            tool_iteration_count=2,
        )
        assert route_after_execute_tools(state, max_iterations=8) == "agent_reason"

    def test_route_after_execute_tools_at_limit_goes_to_draft(self):
        from graph.routing import route_after_execute_tools
        state = _make_state(
            workflow_status="reasoning",
            tool_iteration_count=8,
        )
        assert route_after_execute_tools(state, max_iterations=8) == "draft_response"

    def test_route_after_draft_no_draft_goes_to_failed(self):
        from graph.routing import route_after_draft
        state = _make_state(workflow_status="checking", response_draft=None)
        assert route_after_draft(state) == "end_failed"

    def test_route_after_draft_with_draft_goes_to_check(self):
        from graph.routing import route_after_draft
        state = _make_state(
            workflow_status="checking",
            response_draft="Dear Customer, thank you for contacting us.",
        )
        assert route_after_draft(state) == "check_response"


# ===========================================================================
# Node: check_response (no DB or LLM)
# ===========================================================================

class TestCheckResponseNode:
    """Tests for graph.nodes.node_check_response."""

    def test_clean_draft_passes_with_no_issues(self):
        from graph.nodes import node_check_response
        state = _make_state(
            workflow_status="checking",
            response_draft="We will investigate your missing package.",
            governance_decisions=[],
            validation_errors=[],
        )
        result = node_check_response(state)
        assert result["workflow_status"] == "persisting"
        assert result["validation_errors"] == []

    def test_false_refund_claim_flagged_when_halted(self):
        """Draft claiming 'refund processed' for a HALTED action adds validation warning."""
        from graph.nodes import node_check_response
        from core.models import ExecutionMode, GovernanceDecision

        halted_decision = GovernanceDecision(
            decision=ExecutionMode.HALTED,
            risk_score=0.9,
            explanation="Ineligible refund blocked.",
        )
        state = _make_state(
            workflow_status="checking",
            response_draft="Your refund has been processed and payment returned.",
            governance_decisions=[halted_decision],
            validation_errors=[],
        )
        result = node_check_response(state)
        assert len(result["validation_errors"]) > 0
        assert any("HALTED" in e for e in result["validation_errors"])

    def test_external_send_claim_flagged(self):
        """Draft claiming 'email sent' adds a validation warning."""
        from graph.nodes import node_check_response
        state = _make_state(
            workflow_status="checking",
            response_draft="We have emailed your tracking information.",
            governance_decisions=[],
            validation_errors=[],
        )
        result = node_check_response(state)
        assert len(result["validation_errors"]) > 0


# ===========================================================================
# Integration: node_agent_reason (mock LLM, no DB)
# ===========================================================================

class TestAgentReasonNode:
    """Tests for graph.nodes.node_agent_reason using mock LLM."""

    def test_agent_reason_direct_response_sets_draft(self):
        """Agent producing content with no tool calls sets draft and workflow_status=drafting."""
        from graph.nodes import node_agent_reason
        from core.models import TicketCategory, TicketSentiment, TicketPriority, TicketClassification

        agent = MockSupportAgent(
            response_content="Dear customer, we are sorry for the delay.",
            tool_calls=[],
        )
        state = _make_state(
            workflow_status="reasoning",
            tool_iteration_count=0,
            classification=TicketClassification(
                category=TicketCategory.DELIVERY_STATUS,
                sentiment=TicketSentiment.NEGATIVE,
                priority=TicketPriority.HIGH,
                confidence=0.9,
                reasoning="Package delayed.",
            ),
        )
        result = node_agent_reason(state, agent=agent)
        assert result["workflow_status"] == "drafting"
        assert result["response_draft"] == "Dear customer, we are sorry for the delay."
        assert result["pending_tool_calls"] == []

    def test_agent_reason_with_tool_calls_sets_executing(self):
        """Agent proposing tool calls sets pending_tool_calls and workflow_status=executing."""
        from graph.nodes import node_agent_reason

        mock_tool_call = {
            "id": "tc-001",
            "name": "get_order_details",
            "args": {"order_id": 1},
        }

        class MockAIWithTools(AIMessage):
            def __init__(self):
                super().__init__(content="", tool_calls=[mock_tool_call])

        class AgentWithTools:
            llm = MockLLM()
            model_name = "mock"
            tool_names = []

            def get_system_prompt(self, **kw):
                from agents.personas import get_system_prompt
                return get_system_prompt()

            @property
            def llm_with_tools(self):
                class _LLM:
                    def invoke(self, msgs):
                        return MockAIWithTools()
                return _LLM()

        state = _make_state(
            workflow_status="reasoning",
            tool_iteration_count=0,
        )
        result = node_agent_reason(state, agent=AgentWithTools())
        assert result["workflow_status"] == "executing"
        assert len(result["pending_tool_calls"]) == 1
        assert result["pending_tool_calls"][0]["name"] == "get_order_details"


# ===========================================================================
# Integration: node_execute_tools (governed path, mock gatekeeper)
# ===========================================================================

class TestExecuteToolsNode:
    """Tests that execute_tools node always uses governed_execute."""

    def test_execute_tools_calls_governed_execute(self):
        """Every tool call passes through governed_execute — never directly."""
        from graph.nodes import node_execute_tools

        state = _make_state(
            workflow_status="executing",
            pending_tool_calls=[
                {"id": "tc-1", "name": "get_order_details", "args": {"order_id": 1}},
            ],
            tool_iteration_count=0,
            tool_results=[],
            governance_decisions=[],
            risk_assessments=[],
            audit_ids=[],
        )

        call_log = []

        def mock_governed_execute(tool_name, arguments, run_id=None, approval_handler=None):
            call_log.append((tool_name, arguments))
            from autonomy.gatekeeper import GovernedExecutionResult
            from core.models import (ExecutionMode, GovernanceDecision, RiskAssessment,
                                      RiskLevel, ToolExecutionResult)
            return GovernedExecutionResult(
                run_id=run_id or "mock",
                tool_name=tool_name,
                decision=ExecutionMode.AUTO,
                risk_assessment=RiskAssessment(
                    overall_score=0.1,
                    risk_level=RiskLevel.LOW,
                    requires_human_approval=False,
                    reasoning="Low risk read.",
                ),
                governance_decision=GovernanceDecision(
                    decision=ExecutionMode.AUTO,
                    risk_score=0.1,
                    explanation="Authorized.",
                ),
                executed=True,
                tool_result=ToolExecutionResult(
                    success=True,
                    output={"order_id": 1, "status": "shipped"},
                    tool_name=tool_name,
                ),
            )

        with patch("graph.nodes.governed_execute", side_effect=mock_governed_execute):
            result = node_execute_tools(state)

        assert len(call_log) == 1
        assert call_log[0][0] == "get_order_details"
        assert result["tool_iteration_count"] == 1
        assert len(result["tool_results"]) == 1
        assert result["tool_results"][0]["executed"] is True

    def test_blocked_tool_never_returns_success(self):
        """A HALTED governance decision means executed=False and no success output."""
        from graph.nodes import node_execute_tools

        state = _make_state(
            workflow_status="executing",
            pending_tool_calls=[
                {"id": "tc-2", "name": "execute_refund", "args": {"refund_id": 99}},
            ],
            tool_iteration_count=0,
            tool_results=[],
            governance_decisions=[],
            risk_assessments=[],
            audit_ids=[],
        )

        def mock_halted_execute(tool_name, arguments, run_id=None, approval_handler=None):
            from autonomy.gatekeeper import GovernedExecutionResult
            from core.models import ExecutionMode, GovernanceDecision, RiskAssessment, RiskLevel
            return GovernedExecutionResult(
                run_id=run_id or "mock",
                tool_name=tool_name,
                decision=ExecutionMode.HALTED,
                risk_assessment=RiskAssessment(
                    overall_score=0.99,
                    risk_level=RiskLevel.CRITICAL,
                    requires_human_approval=False,
                    reasoning="Policy violation.",
                ),
                governance_decision=GovernanceDecision(
                    decision=ExecutionMode.HALTED,
                    risk_score=0.99,
                    explanation="Refund blocked: ineligible order.",
                ),
                executed=False,
                error="Refund blocked: ineligible order.",
            )

        with patch("graph.nodes.governed_execute", side_effect=mock_halted_execute):
            result = node_execute_tools(state)

        assert len(result["tool_results"]) == 1
        tr = result["tool_results"][0]
        assert tr["executed"] is False
        assert tr["success"] is False
        assert tr["decision"] == "HALTED"


# ===========================================================================
# Initial state factory
# ===========================================================================

class TestWorkflowState:
    """Tests for graph.state.make_initial_state."""

    def test_initial_state_has_required_fields(self):
        from graph.state import make_initial_state
        state = make_initial_state(
            run_id="test-001",
            ticket_body="Test complaint.",
        )
        assert state["run_id"] == "test-001"
        assert state["ticket_body"] == "Test complaint."
        assert state["workflow_status"] == "intake"
        assert state["tool_iteration_count"] == 0
        assert state["messages"] == []
        assert state["pending_tool_calls"] == []
        assert state["tool_results"] == []
        assert state["ticket_saved"] is False
        assert state["draft_saved"] is False

    def test_initial_state_optional_ids(self):
        from graph.state import make_initial_state
        state = make_initial_state(
            run_id="test-002",
            ticket_body="Complaint",
            customer_id=7,
            order_id=42,
        )
        assert state["customer_id"] == 7
        assert state["order_id"] == 42


# ===========================================================================
# End-to-End LangGraph Workflow Tests (§23.6)
# ===========================================================================

class TestWorkflowEndToEnd:
    """Comprehensive end-to-end tests for the compiled LangGraph workflow."""

    def test_full_graph_direct_response(self):
        """Graph executes from intake to completion with direct response."""
        from graph.workflow import build_support_graph
        from graph.state import make_initial_state

        agent = MockSupportAgent(response_content="We have received your request and are reviewing it.")
        graph = build_support_graph(agent=agent)
        state = make_initial_state(run_id="run-e2e-1", ticket_body="Where is my order?")

        final_state = graph.invoke(state)

        assert final_state["workflow_status"] == "completed"
        assert final_state["response_draft"] == "We have received your request and are reviewing it."
        assert final_state["classification"] is not None

    def test_full_graph_with_governed_tool_execution(self):
        """Agent proposes a tool call, execute_tools governs it, agent drafts final answer."""
        from graph.workflow import build_support_graph
        from graph.state import make_initial_state
        from autonomy.gatekeeper import GovernedExecutionResult
        from core.models import ExecutionMode, GovernanceDecision, RiskAssessment, RiskLevel, ToolExecutionResult

        call_step = 0

        def dynamic_llm_invoke(messages):
            nonlocal call_step
            call_step += 1
            if call_step == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{"id": "call-1", "name": "get_order_details", "args": {"order_id": 101}}],
                )
            else:
                return AIMessage(content="Your order #101 is currently shipped.")

        class DynamicMockAgent:
            def __init__(self):
                self.model_name = "mock"
                self.tool_names = ["get_order_details"]

            def get_system_prompt(self, **kw):
                from agents.personas import get_system_prompt
                return get_system_prompt()

            @property
            def llm(self):
                return self.llm_with_tools

            @property
            def llm_with_tools(self):
                class _LLM:
                    def invoke(self, msgs):
                        return dynamic_llm_invoke(msgs)

                    def with_structured_output(self, schema):
                        return MockStructuredLLM(schema)
                return _LLM()

        def mock_gov_exec(tool_name, arguments, run_id=None, approval_handler=None):
            return GovernedExecutionResult(
                run_id=run_id or "e2e",
                tool_name=tool_name,
                decision=ExecutionMode.AUTO,
                risk_assessment=RiskAssessment(
                    overall_score=0.1,
                    risk_level=RiskLevel.LOW,
                    requires_human_approval=False,
                    reasoning="Read operation.",
                ),
                governance_decision=GovernanceDecision(
                    decision=ExecutionMode.AUTO,
                    risk_score=0.1,
                    explanation="Authorized read.",
                ),
                executed=True,
                tool_result=ToolExecutionResult(
                    success=True,
                    output={"order_id": 101, "status": "shipped"},
                    tool_name=tool_name,
                ),
            )

        agent = DynamicMockAgent()
        graph = build_support_graph(agent=agent)
        state = make_initial_state(run_id="run-e2e-2", ticket_body="Status of order 101?")

        with patch("graph.nodes.governed_execute", side_effect=mock_gov_exec):
            final_state = graph.invoke(state)

        assert final_state["workflow_status"] == "completed"
        assert len(final_state["tool_results"]) == 1
        assert final_state["tool_results"][0]["tool_name"] == "get_order_details"
        assert final_state["tool_results"][0]["executed"] is True
        assert "shipped" in final_state["response_draft"]

    def test_full_graph_with_blocked_mutation(self):
        """Agent proposes a mutation that is HALTED; final state reflects blocked status."""
        from graph.workflow import build_support_graph
        from graph.state import make_initial_state
        from autonomy.gatekeeper import GovernedExecutionResult
        from core.models import ExecutionMode, GovernanceDecision, RiskAssessment, RiskLevel

        call_step = 0

        def dynamic_llm_invoke(messages):
            nonlocal call_step
            call_step += 1
            if call_step == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{"id": "call-halt", "name": "execute_refund", "args": {"order_id": 999, "amount": 500.0}}],
                )
            else:
                return AIMessage(content="I checked our policy and unfortunately a refund cannot be processed for this order.")

        class BlockedMockAgent:
            def __init__(self):
                self.model_name = "mock"
                self.tool_names = ["execute_refund"]

            def get_system_prompt(self, **kw):
                from agents.personas import get_system_prompt
                return get_system_prompt()

            @property
            def llm(self):
                return self.llm_with_tools

            @property
            def llm_with_tools(self):
                class _LLM:
                    def invoke(self, msgs):
                        return dynamic_llm_invoke(msgs)

                    def with_structured_output(self, schema):
                        return MockStructuredLLM(schema)
                return _LLM()

        def mock_halt_exec(tool_name, arguments, run_id=None, approval_handler=None):
            return GovernedExecutionResult(
                run_id=run_id or "e2e-halt",
                tool_name=tool_name,
                decision=ExecutionMode.HALTED,
                risk_assessment=RiskAssessment(
                    overall_score=0.95,
                    risk_level=RiskLevel.CRITICAL,
                    requires_human_approval=False,
                    reasoning="Order ineligible for refund.",
                ),
                governance_decision=GovernanceDecision(
                    decision=ExecutionMode.HALTED,
                    risk_score=0.95,
                    explanation="Policy violation: order is non-refundable.",
                ),
                executed=False,
                error="Refund blocked by policy.",
            )

        agent = BlockedMockAgent()
        graph = build_support_graph(agent=agent)
        state = make_initial_state(run_id="run-e2e-3", ticket_body="Give me a refund for order 999.")

        with patch("graph.nodes.governed_execute", side_effect=mock_halt_exec):
            final_state = graph.invoke(state)

        assert final_state["workflow_status"] == "completed"
        assert len(final_state["tool_results"]) == 1
        assert final_state["tool_results"][0]["executed"] is False
        assert final_state["tool_results"][0]["decision"] == "HALTED"
        assert len(final_state["governance_decisions"]) == 1
        assert final_state["governance_decisions"][0].decision == ExecutionMode.HALTED

    def test_run_support_ticket_runner_integration(self):
        """run_support_ticket returns a well-structured TicketRunResult."""
        from graph.workflow import build_support_graph
        from graph.runner import run_support_ticket, TicketRunResult

        agent = MockSupportAgent(response_content="We are looking into this.")
        graph = build_support_graph(agent=agent)

        result = run_support_ticket(
            ticket_body="Where is package #123?",
            graph=graph,
        )

        assert isinstance(result, TicketRunResult)
        assert result.workflow_status == "completed"
        assert result.category == "DELIVERY_STATUS"
        assert result.response_draft == "We are looking into this."
        assert result.run_id.startswith("run-")

    def test_database_persistence_when_connected(self):
        """node_persist and node_intake correctly interact with database cursor."""
        from graph.nodes import node_intake, node_persist
        from graph.state import make_initial_state

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (555,)  # ticket_id=555
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        class MockPool:
            def __enter__(self):
                return mock_conn

            def __exit__(self, *args):
                pass

        with patch("database.connection.get_connection", return_value=MockPool()):
            # Test intake
            state = make_initial_state(run_id="db-test-1", ticket_body="My order was damaged.")
            intake_res = node_intake(state)
            assert intake_res["ticket_id"] == 555

            # Test persist
            state["ticket_id"] = 555
            state["response_draft"] = "We will replace your item."
            state["customer_id"] = 10
            persist_res = node_persist(state)
            assert persist_res["ticket_saved"] is True
            assert persist_res["draft_saved"] is True

