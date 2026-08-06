"""Tests for ticket qty — always ≥ ticket on the chosen symbol."""

from ticket_size import qty_for_ticket


def test_alkem_ceil_meets_ticket():
    fill = qty_for_ticket(15000, 5654.0)
    assert fill.qty == 3
    assert fill.notional == 16962.0
    assert not fill.undershoot


def test_ultracemco_two_shares_when_one_under_ticket():
    # 1×12146 < 15k → must buy 2 even if ~₹24k
    fill = qty_for_ticket(15000, 12146.0)
    assert fill.qty == 2
    assert fill.notional >= 15000
    assert not fill.undershoot


def test_expensive_single_share_above_ticket():
    fill = qty_for_ticket(15000, 20000.0)
    assert fill.qty == 1
    assert fill.notional == 20000.0


def test_recltd_style():
    fill = qty_for_ticket(15000, 362.05)
    assert fill.qty == 42  # ceil(15000/362.05)
    assert fill.notional >= 15000
