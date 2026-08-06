"""Unit tests for Genius Stock CAR signal (sheet parity)."""

from car import (
    CarSignal,
    average_out_notional,
    car_signal_from_last_n,
    cumulative_averages,
    evaluate_car_position,
    is_in_profit,
    qty_for_average_out,
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


def test_in_profit_exit_guide():
    assert is_in_profit(100, 95)
    assert not is_in_profit(90, 95)


def test_evaluate_average_out_with_rising_closes():
    # Strong uptrend after high → expanding mean rises in the tail
    rising_closes = [100.0 + i for i in range(30)]
    r = evaluate_car_position(
        "TEST",
        rising_closes,
        avg_cost=110.0,
        original_invested=15000.0,
    )
    assert r.signal == CarSignal.AVERAGE_OUT
    assert r.average_out_amount == 1500.0
    assert r.in_profit  # last close 129 >= 110
