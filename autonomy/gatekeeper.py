"""Security gatekeeper and authoritative execution controller (§11.4 & §16).

Serves as the single authoritative execution pathway turning LLM-proposed tool calls
into actual business operations. Enforces:
1. Canonical tool lookup
2. Argument schema validation
3. Conditional context acquisition
4. Contextual risk assessment
5. Governance policy decision (AUTO / ASSISTED / HALTED)
6. Mandatory human approval gating for ASSISTED actions
7. Execution prevention for HALTED and rejected actions (underlying function NEVER called)
8. Persistent audit logging for all decisions and execution outcomes
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from autonomy.approval import ApprovalHandler, TerminalApprovalHandler
from autonomy.audit import record_audit_event
from autonomy.config import GovernanceConfig, get_governance_config
from autonomy.context import RiskContext, acquire_risk_context
from autonomy.evaluator import evaluate_risk
from autonomy.policy import evaluate_governance_policy
from core.models import (
    ActionCategory,
    ExecutionMode,
    GovernanceDecision,
    HumanApprovalResult,
    RiskAssessment,
    RiskLevel,
    ToolDefinition,
    ToolExecutionResult,
)
from customer_support.registry import ToolNotFoundError, get_registry

logger = logging.getLogger(__name__)


class GovernedExecutionResult(BaseModel):
    """Standardized result of a governed tool execution attempt."""
    run_id: str
    tool_name: str
    decision: ExecutionMode
    risk_assessment: RiskAssessment
    governance_decision: GovernanceDecision
    approval_result: Optional[HumanApprovalResult] = None
    executed: bool = False
    tool_result: Optional[ToolExecutionResult] = None
    audit_id: Optional[int] = None
    error: Optional[str] = None


def governed_execute(
    tool_name: str,
    arguments: Dict[str, Any],
    run_id: Optional[str] = None,
    approval_handler: Optional[ApprovalHandler] = None,
    config: Optional[GovernanceConfig] = None,
) -> GovernedExecutionResult:
    """Authoritative execution boundary for all LLM-proposed tool calls.
    
    Guarantees that no protected business function runs without satisfying
    prior argument validation, risk evaluation, policy check, and human approval if gated.
    """
    exec_run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    cfg = config or get_governance_config()
    reg = get_registry()

    # Step 1: Resolve tool through central registry
    tool: Optional[ToolDefinition] = reg.get(tool_name)
    if tool is None:
        err_msg = f"Security Violation: Tool '{tool_name}' is not registered in the canonical registry. Execution blocked."
        audit_id = record_audit_event(
            run_id=exec_run_id,
            tool_name=tool_name,
            arguments=arguments,
            decision=ExecutionMode.HALTED,
            risk_score=1.0,
            explanation=err_msg,
            execution_result={"executed": False, "error": err_msg},
        )
        fake_risk = RiskAssessment(
            overall_score=1.0,
            risk_level=RiskLevel.CRITICAL,
            requires_human_approval=False,
            reasoning=err_msg,
        )
        fake_decision = GovernanceDecision(
            decision=ExecutionMode.HALTED,
            risk_score=1.0,
            policy_violations=["unregistered_tool"],
            explanation=err_msg,
        )
        return GovernedExecutionResult(
            run_id=exec_run_id,
            tool_name=tool_name,
            decision=ExecutionMode.HALTED,
            risk_assessment=fake_risk,
            governance_decision=fake_decision,
            executed=False,
            audit_id=audit_id,
            error=err_msg,
        )

    # Step 2: Validate arguments against tool schema
    try:
        validated_args = tool.arg_schema.model_validate(arguments)
        clean_args = validated_args.model_dump()
    except Exception as val_err:
        err_msg = f"Argument validation failed for tool '{tool.name}': {val_err}"
        audit_id = record_audit_event(
            run_id=exec_run_id,
            tool_name=tool.name,
            arguments=arguments,
            decision=ExecutionMode.HALTED,
            risk_score=0.8,
            explanation=err_msg,
            execution_result={"executed": False, "error": err_msg},
        )
        fake_risk = RiskAssessment(
            overall_score=0.8,
            risk_level=RiskLevel.HIGH,
            requires_human_approval=False,
            reasoning=err_msg,
        )
        fake_decision = GovernanceDecision(
            decision=ExecutionMode.HALTED,
            risk_score=0.8,
            policy_violations=["argument_validation_failed"],
            explanation=err_msg,
        )
        return GovernedExecutionResult(
            run_id=exec_run_id,
            tool_name=tool.name,
            decision=ExecutionMode.HALTED,
            risk_assessment=fake_risk,
            governance_decision=fake_decision,
            executed=False,
            audit_id=audit_id,
            error=err_msg,
        )

    # Step 3: Context acquisition
    ctx: RiskContext = acquire_risk_context(tool, clean_args)

    # Step 4: Risk assessment
    risk_assessment: RiskAssessment = evaluate_risk(ctx, cfg)

    # Step 5: Policy evaluation
    decision: GovernanceDecision = evaluate_governance_policy(tool, risk_assessment, ctx, cfg)

    # Step 6: Enforce decision mode
    # ------------------------------------------------------------------------
    # HALTED: Strictly blocked; tool function MUST NEVER be called
    # ------------------------------------------------------------------------
    if decision.decision == ExecutionMode.HALTED:
        audit_id = record_audit_event(
            run_id=exec_run_id,
            tool_name=tool.name,
            arguments=clean_args,
            decision=ExecutionMode.HALTED,
            risk_score=decision.risk_score,
            explanation=decision.explanation,
            execution_result={"executed": False, "blocked_by_policy": True},
        )
        return GovernedExecutionResult(
            run_id=exec_run_id,
            tool_name=tool.name,
            decision=ExecutionMode.HALTED,
            risk_assessment=risk_assessment,
            governance_decision=decision,
            executed=False,
            audit_id=audit_id,
            error=decision.explanation,
        )

    # ------------------------------------------------------------------------
    # ASSISTED: Gated on explicit human operator approval
    # ------------------------------------------------------------------------
    if decision.decision == ExecutionMode.ASSISTED:
        handler = approval_handler or TerminalApprovalHandler()
        approval_result: HumanApprovalResult = handler.request_approval(
            tool=tool,
            arguments=clean_args,
            decision=decision,
            risk_assessment=risk_assessment,
            ctx=ctx,
        )

        if not approval_result.approved:
            # Human rejected: tool function MUST NEVER be called
            rejection_msg = f"Action '{tool.name}' rejected by human operator ({approval_result.approved_by}): {approval_result.reason}"
            audit_id = record_audit_event(
                run_id=exec_run_id,
                tool_name=tool.name,
                arguments=clean_args,
                decision=ExecutionMode.ASSISTED,
                risk_score=decision.risk_score,
                explanation=rejection_msg,
                execution_result={
                    "executed": False,
                    "human_approval": False,
                    "reviewer": approval_result.approved_by,
                    "reason": approval_result.reason,
                },
            )
            return GovernedExecutionResult(
                run_id=exec_run_id,
                tool_name=tool.name,
                decision=ExecutionMode.ASSISTED,
                risk_assessment=risk_assessment,
                governance_decision=decision,
                approval_result=approval_result,
                executed=False,
                audit_id=audit_id,
                error=rejection_msg,
            )

        # Human approved: proceed to controlled execution
        tool_res = reg.execute(tool.name, clean_args)
        audit_id = record_audit_event(
            run_id=exec_run_id,
            tool_name=tool.name,
            arguments=clean_args,
            decision=ExecutionMode.ASSISTED,
            risk_score=decision.risk_score,
            explanation=f"Action '{tool.name}' executed following explicit approval from {approval_result.approved_by}.",
            execution_result=tool_res.model_dump(),
        )
        return GovernedExecutionResult(
            run_id=exec_run_id,
            tool_name=tool.name,
            decision=ExecutionMode.ASSISTED,
            risk_assessment=risk_assessment,
            governance_decision=decision,
            approval_result=approval_result,
            executed=tool_res.success,
            tool_result=tool_res,
            audit_id=audit_id,
            error=tool_res.error,
        )

    # ------------------------------------------------------------------------
    # AUTO: Authorized for autonomous execution
    # ------------------------------------------------------------------------
    tool_res = reg.execute(tool.name, clean_args)
    audit_id = record_audit_event(
        run_id=exec_run_id,
        tool_name=tool.name,
        arguments=clean_args,
        decision=ExecutionMode.AUTO,
        risk_score=decision.risk_score,
        explanation=decision.explanation,
        execution_result=tool_res.model_dump(),
    )
    return GovernedExecutionResult(
        run_id=exec_run_id,
        tool_name=tool.name,
        decision=ExecutionMode.AUTO,
        risk_assessment=risk_assessment,
        governance_decision=decision,
        executed=tool_res.success,
        tool_result=tool_res,
        audit_id=audit_id,
        error=tool_res.error,
    )
