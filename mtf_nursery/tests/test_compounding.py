"""Unit tests for compounding and sell selection."""

from compounding import (
    ForceTracker,
    OpenPosition,
    compound_after_win,
    estimate_tax,
    pick_sell_candidate,
    unrealized_pct,
)


def test_unrealized_pct():
    pos = OpenPosition("RELIANCE", 15000, 15942)
    assert round(unrealized_pct(pos), 4) == 0.0628


def test_pick_sell_candidate_highest_winner():
    positions = [
        OpenPosition("A", 10000, 10650),
        OpenPosition("B", 10000, 10700),
        OpenPosition("C", 10000, 10500),
    ]
    winner = pick_sell_candidate(positions, profit_target_pct=0.0628)
    assert winner is not None
    assert winner.symbol == "B"


def test_pick_sell_candidate_none_below_target():
    positions = [OpenPosition("A", 10000, 10500)]
    assert pick_sell_candidate(positions, profit_target_pct=0.0628) is None


def test_compound_after_win_splits_50_50():
    buy = 15000.0
    exit_v = round(buy * 1.0628, 2)
    result = compound_after_win(
        buy,
        exit_v,
        current_ticket=15000,
        funded_amount=9000,
        holding_days=30,
    )
    assert result.gross_profit == round(exit_v - buy, 2)
    assert result.net_after_tax == round(result.net_before_tax - result.tax_estimate, 2)
    assert result.self_dividend == round(0.5 * result.net_after_tax, 2)
    assert result.growth == result.self_dividend
    assert result.next_ticket == round(15000 + result.growth, 2)


def test_estimate_tax_zero_on_loss():
    assert estimate_tax(-100) == 0.0


def test_force_tracker_three_then_advance():
    ft = ForceTracker()
    assert ft.record_win() == "F"
    assert ft.record_win() == "F"
    assert ft.record_win() == "F"
    assert ft.force_count == 3
    assert ft.record_win() == "RF"
    assert ft.advance_step_if_ready()
    assert ft.step_no == 2
    assert ft.force_count == 0
