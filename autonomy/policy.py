"""Governance policy engine and authorization layer (§11 & §12.5).

Determines whether proposed actions are:
- AUTO: May execute autonomously without human intervention
- ASSISTED: Requires explicit human review and approval
- HALTED: Strictly blocked due to policy violations, ineligibility, or critical risk

Enforces the core architectural boundary:
The LLM proposes actions; this policy engine decides if they are permitted.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import List, Optional

from autonomy.config import GovernanceConfig, get_governance_config
from autonomy.context import RiskContext
from core.models import (
    ActionCategory,
    ExecutionMode,
    GovernanceDecision,
    RiskAssessment,
    RiskLevel,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


def evaluate_governance_policy(
    tool: ToolDefinition,
    risk_assessment: RiskAssessment,
    ctx: RiskContext,
    config: Optional[GovernanceConfig] = None,
) -> GovernanceDecision:
    """Evaluate business policy and assign authoritative ExecutionMode (AUTO, ASSISTED, HALTED)."""
    cfg = config or get_governance_config()
    violations: List[str] = []
    score = risk_assessment.overall_score

    # ========================================================================
    # 1. Hard Policy Invariants (Immediate HALT)
    # ========================================================================

    # Invariant 1: Missing critical data prevents safe mutation
    if tool.is_mutation and ctx.missing_critical_data:
        violations.extend(ctx.missing_critical_data)

    # Invariant 2: Ineligible refund must NEVER execute (§11.2 & §14)
    if tool.is_mutation and tool.action_category == ActionCategory.FINANCIAL_MUTATION:
        if ctx.eligibility_evaluated and not ctx.is_eligible:
            violations.append(
                f"Refund eligibility policy failed: {'; '.join(ctx.eligibility_violations)}"
            )

    # Invariant 3: Suspended, flagged, or closed customer accounts cannot mutate funds
    if tool.is_mutation and ctx.customer_status in ("suspended", "closed"):
        violations.append(
            f"Customer account #{ctx.customer_id} is '{ctx.customer_status}'. Protected mutations are blocked."
        )

    # Invariant 4: Negative or zero amount on financial mutations
    if tool.action_category == ActionCategory.FINANCIAL_MUTATION and ctx.proposed_amount <= Decimal("0.00"):
        violations.append("Proposed refund amount must be greater than zero.")

    # Invariant 5: Critical Risk Score exceeds upper boundary
    if score > cfg.risk_threshold_assisted:
        violations.append(
            f"Risk score ({int(score * 100)}/100) exceeds safety limit of {int(cfg.risk_threshold_assisted * 100)}/100. Action is HALTED."
        )

    # If any hard invariants were violated, the action is HALTED immediately
    if violations:
        return GovernanceDecision(
            decision=ExecutionMode.HALTED,
            risk_score=score,
            policy_violations=violations,
            approval_required=False,
            explanation=(
                f"Action '{tool.name}' is HALTED by governance policy. Violations: {'; '.join(violations)}."
            ),
            conditions={"hard_stop": True},
        )

    # ========================================================================
    # 2. Autonomous Execution (AUTO)
    # ========================================================================

    # Read-only operations proceed autonomously after argument validation
    if not tool.is_mutation and tool.action_category == ActionCategory.READ:
        return GovernanceDecision(
            decision=ExecutionMode.AUTO,
            risk_score=score,
            policy_violations=[],
            approval_required=False,
            explanation=f"Read-only operation '{tool.name}' authorized for autonomous execution.",
            conditions={"category": "READ"},
        )

    # Communication response draft saving proceeds autonomously
    if tool.action_category == ActionCategory.COMMUNICATION and not tool.side_effect:
        return GovernanceDecision(
            decision=ExecutionMode.AUTO,
            risk_score=score,
            policy_violations=[],
            approval_required=False,
            explanation=f"Drafting operation '{tool.name}' authorized for autonomous internal persistence.",
            conditions={"category": "COMMUNICATION"},
        )

    # Small eligible refund creation within autonomous limit
    if (
        tool.name in ("refund.create_request", "refund.calculate_amount")
        and ctx.is_eligible
        and ctx.proposed_amount <= cfg.auto_max_refund_amount
        and score <= cfg.risk_threshold_auto
    ):
        return GovernanceDecision(
            decision=ExecutionMode.AUTO,
            risk_score=score,
            policy_violations=[],
            approval_required=False,
            explanation=(
                f"Refund creation of ${ctx.proposed_amount:.2f} satisfies all policy requirements "
                f"and low-risk threshold ({int(score * 100)}/100 <= {int(cfg.risk_threshold_auto * 100)}). Autonomous execution authorized."
            ),
            conditions={"tier": "small_eligible_refund"},
        )

    # ========================================================================
    # 3. Assisted Execution (ASSISTED - Human Approval Required)
    # ========================================================================

    # All sensitive financial approvals and executions require human sign-off
    reasons_for_review: List[str] = []

    if tool.name in ("refund.approve_request", "refund.execute"):
        reasons_for_review.append(
            f"Action '{tool.name}' performs protected database payout/state mutation."
        )

    if ctx.proposed_amount > cfg.auto_max_refund_amount:
        reasons_for_review.append(
            f"Proposed amount (${ctx.proposed_amount:.2f}) exceeds autonomous threshold of ${cfg.auto_max_refund_amount:.2f}."
        )

    if score > cfg.risk_threshold_auto:
        reasons_for_review.append(
            f"Risk score ({int(score * 100)}/100) exceeds autonomous limit of {int(cfg.risk_threshold_auto * 100)}/100."
        )

    explanation = (
        f"Action '{tool.name}' requires ASSISTED execution with human approval. "
        f"Justification: {' '.join(reasons_for_review)} Risk assessment: {risk_assessment.reasoning}"
    )

    return GovernanceDecision(
        decision=ExecutionMode.ASSISTED,
        risk_score=score,
        policy_violations=[],
        approval_required=True,
        explanation=explanation,
        conditions={"requires_human_signoff": True},
    )
