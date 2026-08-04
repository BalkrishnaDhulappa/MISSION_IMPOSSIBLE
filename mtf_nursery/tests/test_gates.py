"""Unit tests for buy/sell gates."""

from datetime import date

from gates import BuyGateInput, SellGateInput, count_actions_in_month, count_actions_on_date, evaluate_buy_gate, evaluate_sell_gate


def test_buy_gate_allowed_when_cash_sufficient():
    inp = BuyGateInput(
        free_cash=50000,
        emi_obligation=5000,
        fire_shop_reserve=6000,
        ticket_immediate_need=6000,
        buys_today=0,
        buys_this_month=0,
        max_buys_per_day=1,
        max_mtf_buys_per_month=2,
        buy_blocked_by_rms=False,
    )
    result = evaluate_buy_gate(inp)
    assert result.allowed
    assert result.reasons == ()


def test_buy_gate_blocked_insufficient_cash():
    inp = BuyGateInput(
        free_cash=10000,
        emi_obligation=8000,
        fire_shop_reserve=6000,
        ticket_immediate_need=6000,
        buys_today=0,
        buys_this_month=0,
        max_buys_per_day=1,
        max_mtf_buys_per_month=2,
        buy_blocked_by_rms=False,
    )
    result = evaluate_buy_gate(inp)
    assert not result.allowed
    assert "insufficient_cash_after_obligation" in result.reasons


def test_buy_gate_blocked_by_rms():
    inp = BuyGateInput(
        free_cash=100000,
        emi_obligation=0,
        fire_shop_reserve=6000,
        ticket_immediate_need=6000,
        buys_today=0,
        buys_this_month=0,
        max_buys_per_day=1,
        max_mtf_buys_per_month=2,
        buy_blocked_by_rms=True,
    )
    result = evaluate_buy_gate(inp)
    assert not result.allowed
    assert "rms_buy_blocked" in result.reasons


def test_sell_gate_requires_winner():
    inp = SellGateInput(sells_today=0, max_sells_per_day=1, has_eligible_winner=False)
    result = evaluate_sell_gate(inp)
    assert not result.allowed
    assert "no_eligible_winner" in result.reasons


def test_count_actions_helpers():
    dates = [date(2026, 3, 1), date(2026, 3, 1), date(2026, 3, 15)]
    assert count_actions_on_date(dates, date(2026, 3, 1)) == 2
    assert count_actions_in_month(dates, 2026, 3) == 3
    assert count_actions_in_month(dates, 2026, 4) == 0
