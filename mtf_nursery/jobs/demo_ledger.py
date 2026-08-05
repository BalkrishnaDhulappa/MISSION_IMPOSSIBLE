#!/usr/bin/env python3
"""
Walk through C1 ledger + EMI flow without Zerodha (paper demo).

Creates a sample ₹15k MTF position, shows EMI due alerts,
simulates failed then successful repay verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from config import load_config
from ledger import Ledger


def _print(title: str, data: object) -> None:
    print(f"\n=== {title} ===")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2))
    else:
        print(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF nursery C1 paper demo")
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent.parent / "data" / "demo_ledger.sqlite"),
        help="Demo SQLite path (safe to delete and re-run)",
    )
    parser.add_argument("--fresh", action="store_true", help="Delete existing demo DB first")
    args = parser.parse_args()

    db_path = Path(args.db)
    if args.fresh and db_path.exists():
        db_path.unlink()
        print(f"Removed {db_path}")

    cfg = load_config()
    ledger = Ledger(db_path)
    ledger.ensure_step(1, cfg["ticket_start"])

    buy_date = date(2026, 1, 6)
    pos = ledger.add_position(
        "RELIANCE",
        buy_date,
        qty=10,
        avg_price=1500.0,
        initial_margin=4500.0,
        buffer_pct=cfg["buffer_pct"],
        emi_weeks=cfg["emi_weeks"],
    )
    _print("1) New MTF position (paper)", {
        "symbol": pos.symbol,
        "buy_value": pos.buy_value,
        "initial_margin": pos.initial_margin,
        "funded_baseline": pos.funded_baseline,
        "weekly_emi": pos.weekly_emi,
        "immediate_need": pos.initial_margin + pos.buffer_10pct,
    })

    week1 = buy_date + timedelta(days=7)
    ledger.refresh_emi_statuses(week1)
    _print(f"2) Status on first EMI due ({week1})", ledger.status_summary(week1))

    alerts = ledger.list_emis_needing_alert(week1)
    emi = alerts[0]
    _print("3) Telegram would alert (C3)", {
        "message": f"[MTF][INFO] EMI due: {emi.symbol} ₹{emi.amount}",
        "action": "Kite → Funds → Repay MTF → partial",
        "emi_id": emi.id,
        "status": emi.status,
    })

    fail = ledger.try_verify_emi_from_funded(
        emi.id, funded_now=pos.funded_baseline, tolerance=cfg["emi_verify_tolerance"]
    )
    _print("4) Auto-verify WITHOUT repay (should fail → keep alerting)", {
        "verified": fail.verified,
        "reason": fail.reason,
        "still_pending": len(ledger.list_emis_needing_alert(week1)),
    })

    funded_after = pos.funded_baseline - emi.amount
    ok = ledger.try_verify_emi_from_funded(
        emi.id, funded_now=funded_after, tolerance=cfg["emi_verify_tolerance"]
    )
    _print("5) Auto-verify AFTER simulated Repay MTF", {
        "verified": ok.verified,
        "funded_drop": ok.funded_drop,
        "pending_alerts": len(ledger.list_emis_needing_alert(week1)),
    })

    buy_ok = ledger.evaluate_buy(
        free_cash=25000,
        ticket_immediate_need=pos.initial_margin + pos.buffer_10pct,
        as_of=week1,
        fire_shop_reserve=cfg["fire_shop_daily_reserve"],
        max_buys_per_day=cfg["max_buys_per_day"],
        max_mtf_buys_per_month=cfg["max_mtf_buys_per_month"],
    )
    _print("6) Buy gate with ₹25k cash (likely blocked — EMI obligation)", {
        "allowed": buy_ok.allowed,
        "reasons": list(buy_ok.reasons),
        "remaining_obligation": ledger.remaining_emi_obligation(week1),
    })

    buy_rich = ledger.evaluate_buy(
        free_cash=120000,
        ticket_immediate_need=pos.initial_margin + pos.buffer_10pct,
        as_of=week1,
        fire_shop_reserve=cfg["fire_shop_daily_reserve"],
    )
    _print("7) Buy gate with ₹1.2L cash", {
        "allowed": buy_rich.allowed,
        "reasons": list(buy_rich.reasons),
    })

    print(f"\nDemo DB saved at: {db_path}")
    print("Inspect anytime: python3 jobs/run_status.py --db", db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
