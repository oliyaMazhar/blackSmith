"""Unit and integration tests for Contextual Risk Evaluator (§23.5).

Verifies:
1. Normal eligible refund receives low-risk score and AUTO classification.
2. Elevated risk signals increase the risk score.
3. Limited-edition sell-out with concentrated returns elevates risk score and triggers explanation.
4. Legitimate mass-defect scenario is evaluated using corroborating hardware defect evidence.
5. Risk scores strictly remain within normalized 0.0 - 1.0 (1 - 100 integer) scale.
6. Missing critical context is penalized rather than silently treated as safe.
7. Risk score alone never overrides an absolute policy restriction.
"""

from __future__ import annotations

from decimal import Decimal
import pytest

from autonomy.config import GovernanceConfig, get_governance_config
from autonomy.context import RiskContext, acquire_risk_context
from autonomy.evaluator import evaluate_risk
from autonomy.policy import evaluate_governance_policy
from core.models import ActionCategory, ExecutionMode, RiskLevel, ToolDefinition
from customer_support.registry import get_registry
from database.connection import get_connection


@pytest.fixture(scope="module")
def sample_orders_data():
    """Create sample orders in database for risk evaluation testing."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Clean delivered order on standard non-limited product #3
            cur.execute(
                """
                INSERT INTO orders (customer_id, order_status, payment_status, placed_at)
                VALUES (1, 'delivered', 'paid', CURRENT_TIMESTAMP)
                RETURNING order_id;
                """
            )
            order_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO order_items (order_id, product_id, quantity, unit_price)
                VALUES (%s, 3, 2, 14.99);
                """,
                (order_id,),
            )
        conn.commit()

    yield {
        "standard": {
            "order_id": order_id,
            "customer_id": 1,
            "total": 29.98,
        },
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_records WHERE arguments->>'order_id' = %s;", (str(order_id),))
            cur.execute("DELETE FROM events WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM refunds WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM order_items WHERE order_id = %s;", (order_id,))
            cur.execute("DELETE FROM orders WHERE order_id = %s;", (order_id,))
        conn.commit()


def test_normal_eligible_refund_receives_low_risk(sample_orders_data):
    """Normal eligible refund receives low-risk evaluation on documented scale."""
    reg = get_registry()
    tool = reg.get_tool("refund.create_request")
    std = sample_orders_data["standard"]

    ctx = acquire_risk_context(tool, {"order_id": std["order_id"], "customer_id": std["customer_id"], "amount": 25.0})
    assessment = evaluate_risk(ctx)

    assert assessment.overall_score >= 0.0
    assert assessment.overall_score <= 0.30
    assert assessment.risk_level == RiskLevel.LOW
    assert "satisfies return eligibility criteria" in assessment.reasoning


def test_elevated_risk_signals_increase_score(sample_orders_data):
    """Correlated risk signals explicitly contribute to higher risk score."""
    reg = get_registry()
    tool = reg.get_tool("refund.create_request")
    std = sample_orders_data["standard"]

    ctx = acquire_risk_context(tool, {"order_id": std["order_id"], "customer_id": std["customer_id"], "amount": 25.0})

    # Baseline score without signals
    baseline_assessment = evaluate_risk(ctx)

    # Inject simulated concentration signal
    ctx.correlated_risk_signals.append(
        {"signal_type": "concentration_risk", "severity": "HIGH", "confidence": 0.95}
    )
    elevated_assessment = evaluate_risk(ctx)

    assert elevated_assessment.overall_score > baseline_assessment.overall_score
    # Verify cluster_concentration factor was elevated
    cluster_factors = [f for f in elevated_assessment.risk_factors if f.factor_type == "cluster_concentration"]
    assert len(cluster_factors) == 1
    assert cluster_factors[0].score >= 0.80


def test_limited_edition_return_surge_detected():
    """Limited edition product with concentrated returns triggers anomaly explanation and elevated score (§13.1)."""
    reg = get_registry()
    tool = reg.get_tool("refund.create_request")

    # Product #1 (Limited Edition Wireless Earbuds)
    # Seeded data has 500 sold, 137 returned (>27% return rate) and 40 concentration signals
    ctx = acquire_risk_context(tool, {"order_id": 1, "product_id": 1, "amount": 199.99})
    assessment = evaluate_risk(ctx)

    assert ctx.is_limited_edition is True
    assert ctx.product_return_rate_pct > 20.0  # Return surge threshold
    assert assessment.overall_score >= 0.35  # Elevated out of low-risk AUTO tier
    assert "limited-edition product" in assessment.reasoning or "return surge" in assessment.reasoning


def test_legitimate_mass_defect_scenario_mitigates_fraud():
    """Legitimate widespread defect reports mitigate fraud suspicion (§13.2)."""
    reg = get_registry()
    tool = reg.get_tool("refund.create_request")

    # Synthetic context representing product with 35 customer defect reports
    ctx = RiskContext(
        tool_name="refund.create_request",
        action_category=ActionCategory.FINANCIAL_MUTATION,
        arguments={"order_id": 10, "amount": 50.0},
        order_found=True,
        customer_found=True,
        customer_status="active",
        customer_identity_verified=True,
        eligibility_evaluated=True,
        is_eligible=True,
        defective_batch_reports_count=35,  # >= 15 threshold
        is_limited_edition=False,
        product_return_rate_pct=15.0,
    )

    assessment = evaluate_risk(ctx)

    # Check defect_mitigation factor
    mitigation_factor = next(f for f in assessment.risk_factors if f.factor_type == "defect_mitigation")
    assert mitigation_factor.score == 0.0  # Fully mitigated
    assert "mitigating fraud suspicion" in assessment.reasoning


def test_risk_score_scale_invariant():
    """Risk scores strictly remain within documented scale [0.0, 1.0]."""
    for amount in [0.0, 10.0, 50.0, 150.0, 1000.0]:
        ctx = RiskContext(
            tool_name="refund.create_request",
            action_category=ActionCategory.FINANCIAL_MUTATION,
            arguments={"amount": amount},
            proposed_amount=Decimal(str(amount)),
            order_found=True,
            customer_found=True,
            customer_status="active",
        )
        assessment = evaluate_risk(ctx)
        assert 0.0 <= assessment.overall_score <= 1.0
        assert 0 <= int(assessment.overall_score * 100) <= 100


def test_missing_critical_context_penalized_not_assumed_safe():
    """Missing critical data (e.g. missing order_id) increases score uncertainty rather than assuming safety (§12.2)."""
    tool = get_registry().get_tool("refund.create_request")

    # Complete context
    complete_ctx = RiskContext(
        tool_name="refund.create_request",
        action_category=ActionCategory.FINANCIAL_MUTATION,
        arguments={"order_id": 1, "customer_id": 1, "amount": 20.0},
        order_found=True,
        customer_found=True,
        customer_status="active",
        is_eligible=True,
        eligibility_evaluated=True,
    )
    score_complete = evaluate_risk(complete_ctx).overall_score

    # Context with missing critical data
    missing_ctx = RiskContext(
        tool_name="refund.create_request",
        action_category=ActionCategory.FINANCIAL_MUTATION,
        arguments={"amount": 20.0},
        missing_critical_data=["Order ID missing", "Customer ID unverified"],
    )
    score_missing = evaluate_risk(missing_ctx).overall_score

    assert score_missing > score_complete
    data_factors = [f for f in evaluate_risk(missing_ctx).risk_factors if f.factor_type == "data_completeness"]
    assert len(data_factors) == 1
    assert data_factors[0].score >= 0.70


def test_risk_score_alone_does_not_override_hard_policy():
    """A low risk score cannot authorize execution if a hard policy rule is violated (§12.5)."""
    tool = get_registry().get_tool("refund.create_request")

    # Order cancelled: hard eligibility violation
    ctx = RiskContext(
        tool_name="refund.create_request",
        action_category=ActionCategory.FINANCIAL_MUTATION,
        arguments={"order_id": 99, "amount": 10.0},
        proposed_amount=Decimal("10.00"),
        order_found=True,
        customer_found=True,
        customer_status="active",
        eligibility_evaluated=True,
        is_eligible=False,
        eligibility_violations=["Order status is 'cancelled'."],
    )

    assessment = evaluate_risk(ctx)
    decision = evaluate_governance_policy(tool, assessment, ctx)

    # Policy MUST HALT the action regardless of score
    assert decision.decision == ExecutionMode.HALTED
    assert "Refund eligibility policy failed" in decision.explanation
