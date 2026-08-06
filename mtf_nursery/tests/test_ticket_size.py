"""Tests for ticket qty — ≥ ticket, ≤ max_notional, top affordable D1=A."""

from ticket_size import pick_top_affordable, qty_for_ticket


def test_alkem_ceil_meets_ticket():
    fill = qty_for_ticket(15000, 5654.0)
    assert fill is not None
    assert fill.qty == 3
    assert fill.notional == 16962.0


def test_twenty_k_share_ok():
    fill = qty_for_ticket(15000, 20000.0, max_notional=30000)
    assert fill is not None
    assert fill.qty == 1
    assert fill.notional == 20000.0


def test_over_30k_share_skipped():
    assert qty_for_ticket(15000, 31000.0, max_notional=30000) is None


def test_ultracemco_two_shares_under_30k():
    fill = qty_for_ticket(15000, 12146.0, max_notional=30000)
    assert fill is not None
    assert fill.qty == 2
    assert fill.notional <= 30000


def test_pick_skips_too_expensive_keeps_rank_order():
    cands = [
        {"symbol": "EXPENSIVE", "cmp": 35000.0},
        {"symbol": "RECLTD", "cmp": 362.05},
        {"symbol": "GAIL", "cmp": 174.82},
    ]
    picked = pick_top_affordable(cands, 15000, max_notional=30000)
    assert picked is not None
    sym, fill = picked
    assert sym["symbol"] == "RECLTD"
    assert fill.notional >= 15000
    assert fill.notional <= 30000


def test_recltd_style():
    fill = qty_for_ticket(15000, 362.05)
    assert fill is not None
    assert fill.qty == 42
    assert fill.notional >= 15000
