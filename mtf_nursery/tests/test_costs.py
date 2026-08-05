"""Unit tests for Zerodha cost model."""

from costs import brokerage, interest, pledge_fee, round_trip_costs, square_off_fee


def test_brokerage_capped_at_20():
    assert brokerage(15000) == 20.0
    assert brokerage(5000) == 15.0


def test_pledge_one_side_with_gst():
    assert pledge_fee() == round(15 * 1.18, 2)


def test_interest_per_lakh_per_day():
    assert interest(100000, 10) == 400.0


def test_square_off_fee():
    assert square_off_fee() == round(50 * 1.18, 2)


def test_round_trip_15k_6_28_exit():
    buy_value = 15000.0
    exit_value = round(buy_value * 1.0628, 2)
    funded = 9000.0
    costs = round_trip_costs(buy_value, exit_value, funded, holding_days=30)
    assert costs.buy_brokerage == 20.0
    assert costs.sell_brokerage == 20.0
    assert costs.pledge_both == round(15 * 1.18 * 2, 2)
    assert costs.interest == round(9000 * 0.0004 * 30, 2)
    assert costs.total == round(
        costs.buy_brokerage + costs.sell_brokerage + costs.pledge_both + costs.interest,
        2,
    )
