"""Tests for LIQUIDCASE funding dry-run (C4)."""

from datetime import date

from broker_read import build_account_snapshot
from ledger import Ledger
from liquid_funding import (
    build_liquid_sell_intent,
    evaluate_liquid_funding,
    liquid_idempotency_key,
    run_liquid_funding,
)


def _holdings(cash_liquid_qty=100, liquid_price=100.0, free_cash=5000):
    return [
        {
            "tradingsymbol": "LIQUIDCASE",
            "exchange": "NSE",
            "quantity": cash_liquid_qty,
            "t1_quantity": 0,
            "last_price": liquid_price,
        }
    ]


def _margins(cash):
    return {"available": {"cash": cash, "live_balance": cash}}


def test_evaluate_liquid_funding_shortfall(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    ledger.ensure_step(1, 15000)
    snap = build_account_snapshot(
        _holdings(cash_liquid_qty=250, liquid_price=100.0),
        _margins(5000),
        liquid_etf_symbol="LIQUIDCASE",
    )
    cfg = {
        "fire_shop_daily_reserve": 6000,
        "liquid_etf_min_reserve": 10000,
        "liquid_etf_max_sell_per_event": 25000,
        "liquid_sell_cushion_pct": 0.02,
    }
    shortfall, required, plan = evaluate_liquid_funding(ledger, snap, cfg, date(2026, 8, 4))
    assert shortfall == 1000.0
    assert required == 6000.0
    assert plan.sell_amount > 0


def test_build_liquid_sell_intent_qty():
    holdings = _holdings(cash_liquid_qty=200, liquid_price=100.0)
    snap = build_account_snapshot(holdings, _margins(1000), liquid_etf_symbol="LIQUIDCASE")
    from rms_guard import plan_liquid_topup

    plan = plan_liquid_topup(
        5000,
        snap.liquid_etf_value,
        min_reserve=10000,
        max_sell_per_event=25000,
        cushion_pct=0.02,
        fire_shop_reserve=6000,
    )
    cfg = {
        "liquid_etf_min_reserve": 10000,
        "liquid_etf_max_sell_per_event": 25000,
        "liquid_sell_cushion_pct": 0.02,
        "fire_shop_daily_reserve": 6000,
    }
    intent = build_liquid_sell_intent(snap, holdings, plan, cfg, shortfall=5000)
    assert intent is not None
    assert intent.symbol == "LIQUIDCASE"
    assert intent.product == "CNC"
    assert intent.qty == 100


def test_run_liquid_funding_logs_intent(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    ledger.ensure_step(1, 15000)
    holdings = _holdings(cash_liquid_qty=300, liquid_price=100.0)
    margins = _margins(2000)
    cfg = {
        "mode": "dry_run",
        "liquid_etf_symbol": "LIQUIDCASE",
        "fire_shop_daily_reserve": 6000,
        "liquid_etf_min_reserve": 10000,
        "liquid_etf_max_sell_per_event": 25000,
        "liquid_sell_cushion_pct": 0.02,
        "live_liquid_topup": False,
    }
    result = run_liquid_funding(
        ledger,
        cfg,
        holdings=holdings,
        equity_margins=margins,
        as_of=date(2026, 8, 4),
        send_alerts=False,
    )
    assert result.shortfall == 4000.0
    assert result.intent is not None
    assert "DRY-RUN" in result.message
    assert ledger.log_order_intent(
        "sell",
        "LIQUIDCASE",
        qty=1,
        product="CNC",
        mode="dry_run",
        reason="dup",
        idempotency_key=liquid_idempotency_key(date(2026, 8, 4), "LIQUIDCASE"),
    ) is None


def test_run_liquid_funding_no_shortfall(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    cfg = {
        "mode": "dry_run",
        "liquid_etf_symbol": "LIQUIDCASE",
        "fire_shop_daily_reserve": 6000,
        "liquid_etf_min_reserve": 10000,
        "liquid_etf_max_sell_per_event": 25000,
        "liquid_sell_cushion_pct": 0.02,
    }
    snap_holdings = _holdings(cash_liquid_qty=100, liquid_price=100.0)
    result = run_liquid_funding(
        ledger,
        cfg,
        holdings=snap_holdings,
        equity_margins=_margins(50000),
        as_of=date(2026, 8, 4),
        send_alerts=False,
    )
    assert result.skipped_reason == "no_shortfall"
    assert result.intent is None
