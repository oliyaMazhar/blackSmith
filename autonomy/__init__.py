"""Autonomy, risk assessment, and governance layer for BlackSmith.

Core components:
- governed_execute: Authoritative execution security boundary
- evaluate_risk: Contextual explainable risk evaluator
- evaluate_governance_policy: Policy engine determining AUTO / ASSISTED / HALTED
- acquire_risk_context: Conditional database context retriever
- record_audit_event, get_audit_records: Persistent audit trail in PostgreSQL
- ApprovalHandler, MockApprovalHandler, TerminalApprovalHandler: Human approval interfaces
"""

from autonomy.approval import (
    ApprovalHandler,
    MockApprovalHandler,
    TerminalApprovalHandler,
)
from autonomy.audit import (
    get_audit_records,
    record_audit_event,
)
from autonomy.config import (
    GovernanceConfig,
    get_governance_config,
)
from autonomy.context import (
    RiskContext,
    acquire_risk_context,
)
from autonomy.evaluator import (
    evaluate_risk,
)
from autonomy.gatekeeper import (
    GovernedExecutionResult,
    governed_execute,
)
from autonomy.policy import (
    evaluate_governance_policy,
)

__all__ = [
    "governed_execute",
    "GovernedExecutionResult",
    "evaluate_risk",
    "evaluate_governance_policy",
    "acquire_risk_context",
    "RiskContext",
    "record_audit_event",
    "get_audit_records",
    "ApprovalHandler",
    "MockApprovalHandler",
    "TerminalApprovalHandler",
    "GovernanceConfig",
    "get_governance_config",
]
