"""Unit tests for Genius Stock CAR signal (sheet parity)."""

from car import (
    CarSignal,
    average_out_notional,
    car_signal_from_last_n,
    cumulative_averages,
    evaluate_car_position,
    is_in_profit,
    qty_for_average_out,
    select_profit_target_pct,
)


def test_cumulative_averages_matches_sheet_style():
    closes = [498.30, 497.20, 488.55]
    cas = cumulative_averages(closes)
    assert cas[0] == 498.30
    assert abs(cas[1] - 497.75) < 1e-6
    assert abs(cas[2] - 494.68333333) < 1e-5


def test_signal_average_out_when_10_rising():
    rising = tuple(float(i) for i in range(10, 20))
    assert car_signal_from_last_n(rising) == CarSignal.AVERAGE_OUT


def test_signal_avoid_when_falling():
    falling = tuple(55.67 - i * 0.01 for i in range(10))
    assert car_signal_from_last_n(falling) == CarSignal.AVOID_HOLD


def test_average_out_tenth():
    assert average_out_notional(15177) == 1517.70


def test_qty_skips_if_one_share_above_budget():
    assert qty_for_average_out(1517.70, 2430.0) == 0
    assert qty_for_average_out(1600.0, 800.0) == 2


def test_profit_target_6_28_until_capital_doubles():
    assert select_profit_target_pct(15000, 15000) == 0.0628
    assert select_profit_target_pct(15000, 29999) == 0.0628
    assert select_profit_target_pct(15000, 30000) == 0.0314
    assert select_profit_target_pct(15000, 45000) == 0.0314


def test_in_profit_uses_percentage_target():
    # 6.28% of 100 = 106.28
    assert not is_in_profit(106.0, 100.0, profit_pct=0.0628)
    assert is_in_profit(106.28, 100.0, profit_pct=0.0628)
    # 3.14% of 100 = 103.14
    assert not is_in_profit(103.0, 100.0, profit_pct=0.0314)
    assert is_in_profit(103.14, 100.0, profit_pct=0.0314)


def test_evaluate_average_out_with_rising_closes():
    # Strong uptrend after high → expanding mean rises in the tail
    rising_closes = [100.0 + i for i in range(30)]
    r = evaluate_car_position(
        "TEST",
        rising_closes,
        avg_cost=110.0,
        original_invested=15000.0,
        capital_deployed=15000.0,
    )
    assert r.signal == CarSignal.AVERAGE_OUT
    assert r.average_out_amount == 1500.0
    assert r.profit_target_pct == 0.0628
    assert not r.capital_doubled
    # last close 129; need 110 * 1.0628 = 116.908 → in profit
    assert r.in_profit


def test_evaluate_halves_target_when_capital_doubled():
    rising_closes = [100.0 + i for i in range(30)]
    # CMP 129; avg 126 → 6.28% needs ~133.9 (not in profit), 3.14% needs ~129.96
    # Use avg 125: 6.28% → 132.85 (not), 3.14% → 128.925 (yes at 129)
    r = evaluate_car_position(
        "TEST",
        rising_closes,
        avg_cost=125.0,
        original_invested=15000.0,
        capital_deployed=30000.0,
    )
    assert r.capital_doubled
    assert r.profit_target_pct == 0.0314
    assert r.in_profit

    r2 = evaluate_car_position(
        "TEST",
        rising_closes,
        avg_cost=125.0,
        original_invested=15000.0,
        capital_deployed=15000.0,
    )
    assert not r2.capital_doubled
    assert r2.profit_target_pct == 0.0628
    assert not r2.in_profit
