"""Tests for ticket qty sizing (prefer ~₹15k+, not undershoot)."""

from ticket_size import pick_best_ticket_fill, qty_for_ticket


def test_alkem_prefers_ceil_above_ticket():
    # floor 2×5654=11308 under; ceil 3×5654=16962 (~13% over) → 3
    fill = qty_for_ticket(15000, 5654.0)
    assert fill.qty == 3
    assert fill.notional == 16962.0
    assert not fill.undershoot


def test_ultracemco_stays_one_when_ceil_overshoots_hard():
    # 1×12146 under; 2×24292 = 62% over → keep 1
    fill = qty_for_ticket(15000, 12146.0, max_overshoot_pct=0.25)
    assert fill.qty == 1
    assert fill.undershoot


def test_cheap_stock_meets_ticket():
    fill = qty_for_ticket(15000, 1451.7)  # CIPLA-like
    assert fill.notional >= 15000
    assert fill.qty == 11  # ceil(15000/1451.7)


def test_share_already_above_ticket():
    fill = qty_for_ticket(15000, 16000.0)
    assert fill.qty == 1


def test_pick_best_prefers_closer_above():
    cands = [
        {"symbol": "ALKEM", "cmp": 5654.0},
        {"symbol": "GAIL", "cmp": 174.82},
        {"symbol": "ULTRACEMCO", "cmp": 12146.0},
    ]
    picked = pick_best_ticket_fill(cands, 15000)
    assert picked is not None
    sym, fill = picked
    assert sym["symbol"] == "GAIL"
    assert fill.notional >= 15000
    assert abs(fill.notional - 15000) < 100
