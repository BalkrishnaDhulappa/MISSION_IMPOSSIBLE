#!/usr/bin/env python3
"""Real dry-run: read Zerodha via Kite, alert on EMI/RMS — no orders placed."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from config import load_config
from dry_run import run_dry_cycle
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from liquid_funding import run_liquid_funding
from notify import Level, send_telegram


def main() -> int:
    root = bootstrap()
    parser = argparse.ArgumentParser(description="MTF nursery real dry-run (read-only)")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None, help="e.g. /home/ubuntu/.env_fire_shop")
    parser.add_argument("--token-path", default=None, help="e.g. /home/ubuntu/fire_shop/.kite_token")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument(
        "--sync-mtf",
        action="store_true",
        help="Import open MTF holdings from Kite into ledger if missing",
    )
    parser.add_argument("--json", action="store_true", help="Print report JSON to stdout")
    args = parser.parse_args()

    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    cfg["mode"] = "dry_run"

    token_path = args.token_path or cfg.get("kite_token_path") or root / ".kite_token"
    ledger = Ledger(args.db)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    try:
        kite = get_kite(token_path=token_path)
    except KiteConfigError as exc:
        msg = f"Kite setup failed: {exc}"
        print(msg, file=sys.stderr)
        if not args.no_telegram:
            send_telegram(msg, level=Level.ERROR)
        return 1

    if args.sync_mtf:
        from broker_read import build_account_snapshot

        holdings = kite.holdings()
        margins = kite.margins()["equity"]
        snap = build_account_snapshot(
            holdings, margins, liquid_etf_symbol=cfg.get("liquid_etf_symbol", "LIQUIDCASE")
        )
        for h in snap.mtf_holdings:
            imported = ledger.import_mtf_from_broker(
                h.symbol,
                h.quantity,
                h.average_price,
                h.initial_margin,
                buy_date=as_of,
            )
            if imported:
                print(f"Synced ledger position: {h.symbol}")

    report = run_dry_cycle(
        ledger,
        cfg,
        kite=kite,
        as_of=as_of,
        send_alerts=not args.no_telegram,
        skip_liquid_funding=True,
    )

    liquid = run_liquid_funding(
        ledger,
        cfg,
        kite=kite,
        as_of=as_of,
        send_alerts=not args.no_telegram,
    )

    if args.json:
        out = {
            "as_of": report.as_of.isoformat(),
            "mode": report.mode,
            "errors": report.errors,
            "emi_verified": report.emi_verified,
            "emi_alerts_sent": report.emi_alerts_sent,
            "rms_severity": report.rms_severity,
            "rms_messages": report.rms_messages,
            "liquid_topup_intent": report.liquid_topup_intent or liquid.message,
            "buy_gate_message": report.buy_gate_message,
            "ledger_summary": report.ledger_summary,
        }
        if report.snapshot:
            out["account"] = {
                "free_cash": report.snapshot.free_cash,
                "liquid_etf_value": report.snapshot.liquid_etf_value,
                "mtf_holdings": [
                    {
                        "symbol": h.symbol,
                        "qty": h.quantity,
                        "funded_estimate": h.funded_estimate,
                    }
                    for h in report.snapshot.mtf_holdings
                ],
            }
        print(json.dumps(out, indent=2))
    else:
        print(f"MTF dry-run {report.as_of} mode={report.mode}")
        if report.errors:
            for e in report.errors:
                print(f"  ERROR: {e}")
        if report.snapshot:
            print(f"  Cash: ₹{report.snapshot.free_cash:,.2f}")
            print(f"  LIQUIDCASE: ₹{report.snapshot.liquid_etf_value:,.2f}")
            print(f"  MTF holdings: {len(report.snapshot.mtf_holdings)}")
        for v in report.emi_verified:
            print(f"  {v}")
        for a in report.emi_alerts_sent:
            print(f"  ALERT: {a}")
        if report.liquid_topup_intent:
            print(f"  {report.liquid_topup_intent}")
        elif liquid.message:
            print(f"  {liquid.message}")
        print(f"  {report.buy_gate_message}")

    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
