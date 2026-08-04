"""Tests for broker_read."""

from broker_read import build_account_snapshot, parse_mtf_holding


def _holding(symbol: str, mtf_qty: int, mtf_value: float, margin: float, price: float = 100):
    return {
        "tradingsymbol": symbol,
        "exchange": "NSE",
        "quantity": 0,
        "t1_quantity": 0,
        "last_price": price,
        "mtf": {
            "quantity": mtf_qty,
            "used_quantity": 0,
            "average_price": mtf_value / mtf_qty if mtf_qty else 0,
            "value": mtf_value,
            "initial_margin": margin,
        },
    }


def test_parse_mtf_holding():
    row = _holding("RELIANCE", 10, 15000, 4500, 1500)
    view = parse_mtf_holding(row)
    assert view is not None
    assert view.funded_estimate == 10500.0


def test_build_account_snapshot():
    holdings = [
        _holding("RELIANCE", 10, 15000, 4500),
        {
            "tradingsymbol": "LIQUIDCASE",
            "quantity": 100,
            "t1_quantity": 0,
            "last_price": 110,
            "mtf": {"quantity": 0, "value": 0, "initial_margin": 0},
        },
    ]
    margins = {"available": {"cash": 25000, "live_balance": 30000}}
    snap = build_account_snapshot(holdings, margins)
    assert snap.free_cash == 30000
    assert len(snap.mtf_holdings) == 1
    assert snap.liquid_etf_value == 11000
