"""Tests for dry_run orchestration with mocked broker data."""

from datetime import date

from broker_read import build_account_snapshot
from dry_run import run_dry_cycle
from ledger import Ledger


def _mock_holdings():
    return [
        {
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "quantity": 0,
            "t1_quantity": 0,
            "last_price": 1500,
            "mtf": {
                "quantity": 10,
                "average_price": 1500,
                "value": 15000,
                "initial_margin": 4500,
            },
        }
    ]


def _mock_margins(cash=100000):
    return {"available": {"cash": cash, "live_balance": cash}}


def test_dry_cycle_verifies_emi(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    ledger.ensure_step(1, 15000)
    buy = date(2026, 1, 6)
    ledger.add_position("RELIANCE", buy, 10, 1500, 4500)
    due = date(2026, 1, 13)
    ledger.refresh_emi_statuses(due)
    emi = ledger.list_emis_needing_alert(due)
    holdings = _mock_holdings()
    # funded dropped by 562.5
    holdings[0]["mtf"]["initial_margin"] = 5062.5
    margins = _mock_margins()

    cfg = {
        "mode": "dry_run",
        "ticket_start": 15000,
        "emi_verify_tolerance": 50,
        "fire_shop_daily_reserve": 6000,
        "liquid_etf_symbol": "LIQUIDCASE",
        "liquid_etf_min_reserve": 10000,
        "liquid_etf_max_sell_per_event": 25000,
        "liquid_sell_cushion_pct": 0.02,
        "buffer_pct": 0.10,
        "max_buys_per_day": 1,
        "max_mtf_buys_per_month": 2,
    }
    report = run_dry_cycle(
        ledger,
        cfg,
        holdings=holdings,
        equity_margins=margins,
        as_of=due,
        send_alerts=False,
    )
    assert not report.errors
    assert len(report.emi_verified) == 1
    assert len(ledger.list_emis_needing_alert(due)) == 0


def test_dry_cycle_alerts_when_not_repaid(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    ledger.ensure_step(1, 15000)
    buy = date(2026, 1, 6)
    ledger.add_position("RELIANCE", buy, 10, 1500, 4500)
    due = date(2026, 1, 13)
    ledger.refresh_emi_statuses(due)

    report = run_dry_cycle(
        ledger,
        cfg={
            "mode": "dry_run",
            "ticket_start": 15000,
            "emi_verify_tolerance": 50,
            "fire_shop_daily_reserve": 6000,
            "liquid_etf_symbol": "LIQUIDCASE",
            "liquid_etf_min_reserve": 10000,
            "liquid_etf_max_sell_per_event": 25000,
            "liquid_sell_cushion_pct": 0.02,
            "buffer_pct": 0.10,
            "max_buys_per_day": 1,
            "max_mtf_buys_per_month": 2,
        },
        holdings=_mock_holdings(),
        equity_margins=_mock_margins(),
        as_of=due,
        send_alerts=False,
    )
    assert len(report.emi_alerts_sent) == 1
