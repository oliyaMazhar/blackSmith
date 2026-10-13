"""Contextual risk evaluator for BlackSmith (§12).

Calculates an explainable, deterministic risk score on a 1 - 100 scale
(normalized to 0.0 - 1.0 for relational schema and governance compatibility).
Computes explicit factor weights, identifies mitigating and aggravating evidence,
and separates risk evaluation from policy authorization.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import List, Optional, Tuple

from autonomy.config import GovernanceConfig, get_governance_config
from autonomy.context import RiskContext
from core.models import ActionCategory, RiskAssessment, RiskFactor, RiskLevel

logger = logging.getLogger(__name__)


def evaluate_risk(
    ctx: RiskContext,
    config: Optional[GovernanceConfig] = None,
) -> RiskAssessment:
    """Evaluate business risk for a proposed tool action using grounded evidence.
    
    Returns structured RiskAssessment with factor contributions and explainability.
    """
    cfg = config or get_governance_config()
    factors: List[RiskFactor] = []

    # 1. Read-only actions have minimal inherent risk
    if ctx.action_category == ActionCategory.READ:
        factor = RiskFactor(
            factor_type="read_operation",
            description="Read-only data retrieval without business state modification.",
            weight=1.0,
            score=0.05,
            details={"action_category": "READ"},
        )
        return RiskAssessment(
            overall_score=0.05,
            risk_level=RiskLevel.LOW,
            risk_factors=[factor],
            requires_human_approval=False,
            reasoning="Read-only operation has minimal risk and produces zero side effects.",
        )

    # 2. Factor: Financial Impact
    fin_score = 0.10
    fin_details = {"proposed_amount": float(ctx.proposed_amount)}
    if ctx.proposed_amount > Decimal("0.00"):
        if ctx.proposed_amount <= cfg.auto_max_refund_amount:
            fin_score = 0.20
            fin_details["tier"] = "small_within_auto_limit"
        elif ctx.proposed_amount <= cfg.high_value_refund_amount:
            fin_score = 0.55
            fin_details["tier"] = "medium_value"
        else:
            fin_score = 0.90
            fin_details["tier"] = "high_value_exceeds_threshold"
    else:
        # Non-financial mutation (e.g. ticket drafting)
        fin_score = 0.10

    factors.append(
        RiskFactor(
            factor_type="financial_impact",
            description=f"Evaluates financial magnitude of proposed amount (${ctx.proposed_amount:.2f}).",
            weight=cfg.weight_financial_impact,
            score=fin_score,
            details=fin_details,
        )
    )

    # 3. Factor: Eligibility Compliance
    elig_score = 0.10
    elig_details = {"eligibility_evaluated": ctx.eligibility_evaluated}
    if ctx.eligibility_evaluated:
        if ctx.is_eligible:
            elig_score = 0.05
            elig_details["status"] = "eligible"
        else:
            elig_score = 1.00
            elig_details["status"] = "ineligible"
            elig_details["violations"] = ctx.eligibility_violations
    else:
        if ctx.action_category == ActionCategory.FINANCIAL_MUTATION:
            elig_score = 0.80
            elig_details["status"] = "unevaluated_mutation"

    factors.append(
        RiskFactor(
            factor_type="eligibility_compliance",
            description="Evaluates adherence to return window, payment status, and balance limits.",
            weight=cfg.weight_eligibility_compliance,
            score=elig_score,
            details=elig_details,
        )
    )

    # 4. Factor: Account Standing & Customer History
    cust_score = 0.10
    cust_details = {
        "status": ctx.customer_status,
        "verified": ctx.customer_identity_verified,
        "lifetime_refunds": ctx.customer_lifetime_refunds_count,
    }
    if ctx.customer_status == "active":
        if ctx.customer_identity_verified:
            cust_score = 0.05
        else:
            cust_score = 0.20
        # Elevate if customer has high refund frequency
        if ctx.customer_lifetime_refunds_count >= 3:
            cust_score = min(1.0, cust_score + 0.30)
    elif ctx.customer_status == "flagged":
        cust_score = 0.85
    elif ctx.customer_status in ("suspended", "closed"):
        cust_score = 1.00
    else:
        cust_score = 0.50

    factors.append(
        RiskFactor(
            factor_type="account_standing",
            description="Evaluates customer standing, account verification, and historical refund frequency.",
            weight=cfg.weight_account_standing,
            score=cust_score,
            details=cust_details,
        )
    )

    # 5. Factor: Cluster Concentration & Anomaly Detection
    cluster_score = 0.05
    cluster_details = {
        "is_limited_edition": ctx.is_limited_edition,
        "return_rate_pct": ctx.product_return_rate_pct,
        "shared_accounts": ctx.shared_fingerprint_accounts_count,
        "cluster_refunds": ctx.shared_cluster_refund_count,
    }

    # Check coordinated syndicate behavior
    if (
        ctx.shared_fingerprint_accounts_count >= cfg.syndicate_min_shared_accounts
        and ctx.shared_cluster_refund_count >= cfg.syndicate_min_cluster_refunds
    ):
        cluster_score = 0.95
        cluster_details["anomaly"] = "coordinated_syndicate_cluster"
    elif ctx.is_limited_edition and ctx.product_return_rate_pct >= cfg.limited_edition_return_surge_pct:
        cluster_score = 0.85
        cluster_details["anomaly"] = "limited_edition_return_surge"
    elif ctx.shared_fingerprint_accounts_count >= cfg.syndicate_min_shared_accounts:
        cluster_score = 0.50
        cluster_details["anomaly"] = "shared_fingerprints_detected"

    # Add signal corroboration if existing signals present
    concentration_signals = [
        s for s in ctx.correlated_risk_signals if s.get("signal_type") in ("concentration_risk", "syndicate_cluster")
    ]
    if concentration_signals:
        cluster_score = max(cluster_score, 0.90)
        cluster_details["corroborating_signals_count"] = len(concentration_signals)

    factors.append(
        RiskFactor(
            factor_type="cluster_concentration",
            description="Evaluates coordinated account clustering and limited-edition return surges.",
            weight=cfg.weight_cluster_concentration,
            score=cluster_score,
            details=cluster_details,
        )
    )

    # 6. Factor: Defect Mitigation vs Fraud
    # If a product has a documented widespread defect, high return volume is legitimate, not syndicate fraud
    defect_score = 0.10
    defect_details = {"defect_reports_count": ctx.defective_batch_reports_count}
    is_legitimate_mass_defect = ctx.defective_batch_reports_count >= cfg.legitimate_defect_report_threshold
    defect_details["is_legitimate_mass_defect"] = is_legitimate_mass_defect

    if is_legitimate_mass_defect:
        # High defect volume mitigates fraud suspicion (score decreases to 0.0)
        defect_score = 0.00
        defect_details["mitigation"] = "Corroborated hardware defect advisory reduces fraud suspicion."
        # Also adjust cluster score down if it was raised solely by return surge
        if cluster_details.get("anomaly") == "limited_edition_return_surge":
            cluster_score = 0.35
    else:
        defect_score = 0.10

    factors.append(
        RiskFactor(
            factor_type="defect_mitigation",
            description="Corroborates hardware defect reports to distinguish legitimate returns from fraud.",
            weight=cfg.weight_defect_mitigation,
            score=defect_score,
            details=defect_details,
        )
    )

    # 7. Factor: Data Completeness & Missing Context
    missing_score = 0.05
    missing_details = {"missing_items": ctx.missing_critical_data}
    if ctx.missing_critical_data:
        # Missing evidence is penalized, never treated as proof of safety (§12.2)
        missing_score = min(1.0, 0.40 * len(ctx.missing_critical_data))
        missing_details["penalty"] = "Uncertainty penalty applied for missing critical records."

    factors.append(
        RiskFactor(
            factor_type="data_completeness",
            description="Penalizes missing critical data (unknown customer, unverified order).",
            weight=cfg.weight_data_completeness,
            score=missing_score,
            details=missing_details,
        )
    )

    # Calculate weighted composite score
    composite_score = sum(f.weight * f.score for f in factors)
    composite_score = max(0.01, min(1.0, composite_score))
    normalized_score = round(composite_score, 4)

    # Map to Risk Level
    if normalized_score <= cfg.risk_threshold_auto:
        level = RiskLevel.LOW
    elif normalized_score <= 0.60:
        level = RiskLevel.MEDIUM
    elif normalized_score <= cfg.risk_threshold_assisted:
        level = RiskLevel.HIGH
    else:
        level = RiskLevel.CRITICAL

    requires_approval = (
        normalized_score > cfg.risk_threshold_auto
        or (ctx.proposed_amount > cfg.auto_max_refund_amount)
        or not ctx.is_eligible
    )

    # Build human-readable explanation
    elevated_factors = [f.factor_type for f in factors if f.score >= 0.50]
    explanation_parts = [
        f"Evaluated risk score: {int(normalized_score * 100)}/100 (level={level.value})."
    ]

    if not ctx.is_eligible and ctx.eligibility_violations:
        explanation_parts.append(f"Business policy violations: {'; '.join(ctx.eligibility_violations)}.")
    elif ctx.is_eligible:
        explanation_parts.append("Order satisfies return eligibility criteria.")

    if cluster_details.get("anomaly") == "coordinated_syndicate_cluster":
        explanation_parts.append(
            f"Detected coordinated syndicate cluster: {ctx.shared_fingerprint_accounts_count} accounts sharing device/shipping fingerprints with {ctx.shared_cluster_refund_count} recent refunds."
        )
    elif cluster_details.get("anomaly") == "limited_edition_return_surge":
        explanation_parts.append(
            f"Elevated return surge ({ctx.product_return_rate_pct:.1f}% returns) on limited-edition product '{ctx.product_name}'."
        )

    if is_legitimate_mass_defect:
        explanation_parts.append(
            f"Corroborating evidence: {ctx.defective_batch_reports_count} customers reported hardware defects, mitigating fraud suspicion."
        )

    if ctx.missing_critical_data:
        explanation_parts.append(f"Missing data penalty: {', '.join(ctx.missing_critical_data)}.")

    reasoning = " ".join(explanation_parts)

    return RiskAssessment(
        overall_score=normalized_score,
        risk_level=level,
        risk_factors=factors,
        requires_human_approval=requires_approval,
        reasoning=reasoning,
    )
