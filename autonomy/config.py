"""Governance policy thresholds, weights, and configuration for BlackSmith.

Centralizes all numerical thresholds, scoring weights, and execution limits
in a single location to ensure consistent and explainable evaluations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict
from pydantic import BaseModel, Field


class GovernanceConfig(BaseModel):
    """Central configuration for risk scoring and execution mode policies."""

    # Financial thresholds
    auto_max_refund_amount: Decimal = Field(
        default=Decimal("50.00"),
        description="Maximum dollar amount that can execute in AUTO mode without human approval.",
    )
    high_value_refund_amount: Decimal = Field(
        default=Decimal("150.00"),
        description="Dollar amount above which financial impact factor reaches maximum severity.",
    )

    # Score thresholds on 0.0 - 1.0 normalized scale (equivalent to 0 - 100 integer scale)
    risk_threshold_auto: float = Field(
        default=0.30,
        description="Upper score limit for AUTO mode. Actions scoring above this require human review or halt.",
    )
    risk_threshold_assisted: float = Field(
        default=0.75,
        description="Upper score limit for ASSISTED mode. Actions scoring above this are HALTED.",
    )

    # Factor weights for composite risk scoring (must sum to 1.0)
    weight_financial_impact: float = Field(default=0.20)
    weight_eligibility_compliance: float = Field(default=0.30)
    weight_account_standing: float = Field(default=0.15)
    weight_cluster_concentration: float = Field(default=0.20)
    weight_defect_mitigation: float = Field(default=0.10)
    weight_data_completeness: float = Field(default=0.05)

    # Syndicate & anomaly detection thresholds
    syndicate_min_shared_accounts: int = Field(
        default=3,
        description="Minimum number of accounts sharing fingerprints to trigger cluster anomaly.",
    )
    syndicate_min_cluster_refunds: int = Field(
        default=5,
        description="Minimum refund requests across cluster to indicate coordinated syndicate activity.",
    )
    limited_edition_return_surge_pct: float = Field(
        default=20.0,
        description="Return rate percentage above which limited edition product is flagged for concentrated returns.",
    )
    legitimate_defect_report_threshold: int = Field(
        default=15,
        description="Distinct customer defect reports needed to substantiate a legitimate mass-defect pattern.",
    )

    # Lookback windows (days)
    correlation_window_days: int = Field(default=30)


# Global default configuration instance
_DEFAULT_CONFIG = GovernanceConfig()


def get_governance_config() -> GovernanceConfig:
    """Retrieve the active governance configuration."""
    return _DEFAULT_CONFIG
