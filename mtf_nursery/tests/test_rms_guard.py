"""Unit tests for RMS guard thresholds and LIQUIDCASE top-up."""

from rms_guard import (
    PositionRisk,
    RmsSeverity,
    account_cash_severity,
    loss_pct_vs_funded,
    max_severity,
    plan_liquid_topup,
    position_risk_severity,
)


def test_loss_pct_vs_funded():
    assert loss_pct_vs_funded(8000, 10000) == 0.2
    assert loss_pct_vs_funded(10000, 10000) == 0.0


def test_position_risk_severity_bands():
    pos = PositionRisk("X", mtm_value=8500, funded_amount=10000)
    assert position_risk_severity(pos, warn_pct=0.15, critical_pct=0.20) == RmsSeverity.WARN
    pos_crit = PositionRisk("X", mtm_value=7900, funded_amount=10000)
    assert position_risk_severity(pos_crit) == RmsSeverity.CRITICAL


def test_account_cash_severity():
    assert account_cash_severity(5000, 10000) == RmsSeverity.CRITICAL
    assert account_cash_severity(20000, 10000) == RmsSeverity.OK


def test_max_severity():
    assert max_severity(RmsSeverity.OK, RmsSeverity.WARN) == RmsSeverity.WARN


def test_plan_liquid_topup_no_shortfall():
    plan = plan_liquid_topup(0, liquid_etf_value=50000, min_reserve=10000, max_sell_per_event=25000)
    assert plan.sell_amount == 0.0
    assert plan.reasons == ("no_shortfall",)


def test_plan_liquid_topup_respects_min_reserve():
    plan = plan_liquid_topup(
        5000,
        liquid_etf_value=12000,
        min_reserve=10000,
        max_sell_per_event=25000,
    )
    assert plan.below_min_reserve
    assert plan.sell_amount == 2000.0
    assert "insufficient_liquid_above_reserve" in plan.reasons


def test_plan_liquid_topup_sells_with_cushion():
    plan = plan_liquid_topup(
        5000,
        liquid_etf_value=50000,
        min_reserve=10000,
        max_sell_per_event=25000,
        cushion_pct=0.02,
        fire_shop_reserve=6000,
    )
    expected = min(round(5000 * 1.02 + 6000, 2), 40000, 25000)
    assert plan.sell_amount == expected
    assert not plan.below_min_reserve
