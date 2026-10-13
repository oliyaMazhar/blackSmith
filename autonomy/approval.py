"""Human approval mechanism for ASSISTED governance execution (§11.3).

Provides testable and interactive supervisor review interfaces.
Presents all required evidence, risk scores, and consequences to the human operator.
Enforces that the LLM cannot approve its own actions and rejections prevent execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Protocol

from autonomy.context import RiskContext
from core.models import (
    GovernanceDecision,
    HumanApprovalResult,
    RiskAssessment,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class ApprovalHandler(Protocol):
    """Protocol for human approval handlers (terminal interactive or test fixture)."""

    def request_approval(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        decision: GovernanceDecision,
        risk_assessment: RiskAssessment,
        ctx: RiskContext,
    ) -> HumanApprovalResult:
        """Prompt or evaluate human review, returning structured HumanApprovalResult."""
        ...


class MockApprovalHandler:
    """Configurable approval handler for automated testing without blocking terminal."""

    def __init__(
        self,
        approved: bool = True,
        approved_by: str = "test_human_supervisor",
        reason: str = "Authorized per test scenario.",
    ) -> None:
        self.approved = approved
        self.approved_by = approved_by
        self.reason = reason
        self.calls_count = 0
        self.last_tool_name: Optional[str] = None

    def request_approval(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        decision: GovernanceDecision,
        risk_assessment: RiskAssessment,
        ctx: RiskContext,
    ) -> HumanApprovalResult:
        self.calls_count += 1
        self.last_tool_name = tool.name
        return HumanApprovalResult(
            approved=self.approved,
            approved_by=self.approved_by,
            reason=self.reason,
            timestamp=datetime.now(timezone.utc),
            signature=f"sig-{self.approved_by}-{int(datetime.now(timezone.utc).timestamp())}",
        )


class TerminalApprovalHandler:
    """Interactive terminal approval handler presenting structured context to operator."""

    def __init__(self, input_func: Optional[Callable[[str], str]] = None) -> None:
        self._input = input_func or input

    def request_approval(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        decision: GovernanceDecision,
        risk_assessment: RiskAssessment,
        ctx: RiskContext,
    ) -> HumanApprovalResult:
        print("\n" + "=" * 65)
        print("  GOVERNANCE ALERT: HUMAN APPROVAL REQUIRED (ASSISTED MODE)")
        print("=" * 65)
        print(f"  Proposed Action  : {tool.name}")
        print(f"  Action Category  : {tool.action_category.value}")
        print(f"  Risk Level       : {risk_assessment.risk_level.value} ({int(risk_assessment.overall_score * 100)}/100)")
        print(f"  Side Effect      : {'MUTATION OF BUSINESS STATE' if tool.is_mutation else 'Read Only'}")
        print(f"  Arguments        : {arguments}")
        print("-" * 65)
        print("  Supporting Evidence & Assessment:")
        print(f"  {risk_assessment.reasoning}")

        if ctx.eligibility_evaluated:
            status_text = "PASSED" if ctx.is_eligible else f"FAILED ({'; '.join(ctx.eligibility_violations)})"
            print(f"  Eligibility Checks: {status_text}")

        print("-" * 65)
        print("  Consequences:")
        print("  - Approving this action will immediately execute the database mutation.")
        print("  - Rejecting this action will cancel execution and record an audit rejection.")
        print("=" * 65)

        while True:
            try:
                choice = self._input("  Approve this proposed action? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "n"

            if choice in ("y", "yes"):
                reviewer = "human_operator_terminal"
                reason = "Operator manually approved action after inspecting risk context."
                return HumanApprovalResult(
                    approved=True,
                    approved_by=reviewer,
                    reason=reason,
                    timestamp=datetime.now(timezone.utc),
                    signature=f"terminal-approval-{int(datetime.now(timezone.utc).timestamp())}",
                )
            elif choice in ("n", "no", ""):
                reviewer = "human_operator_terminal"
                reason = "Operator manually rejected proposed action."
                return HumanApprovalResult(
                    approved=False,
                    approved_by=reviewer,
                    reason=reason,
                    timestamp=datetime.now(timezone.utc),
                    signature=None,
                )
            else:
                print("  Invalid choice. Please enter 'y' to approve or 'n' to reject.")
