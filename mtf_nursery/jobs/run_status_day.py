#!/usr/bin/env python3
"""End-of-day status — ledger + broker summary to Telegram."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from broker_read import build_account_snapshot
from config import load_config
from dry_run import run_dry_cycle
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from notify import Level, send_telegram
from scanner_fetch import load_scan_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--token-path", default=None)
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    ledger = Ledger(args.db)
    today = date.today()

    summary = ledger.status_summary(today)
    top = None
    scan_path = Path(cfg.get("scan_output", "data/last_scan.json"))
    if not scan_path.is_absolute():
        scan_path = root / scan_path
    if scan_path.exists():
        cands = load_scan_result(scan_path).get("candidates") or []
        top = cands[0]["symbol"] if cands else None

    broker_line = ""
    try:
        kite = get_kite(token_path=args.token_path or cfg.get("kite_token_path"))
        report = run_dry_cycle(ledger, cfg, kite=kite, send_alerts=False)
        if report.snapshot:
            s = report.snapshot
            broker_line = (
                f"cash ₹{s.free_cash:,.0f} | MTF {len(s.mtf_holdings)} | "
                f"LIQUID {s.liquid_etf_symbol} ₹{s.liquid_etf_value:,.0f}"
            )
    except KiteConfigError:
        broker_line = "broker: token unavailable"

    msg = (
        f"EOD {today} | open MTF {summary['open_positions']} | "
        f"EMI due ₹{summary['remaining_emi_obligation']:,.0f} | "
        f"scan top {top or 'n/a'} | {broker_line}"
    )
    print(json.dumps({"summary": summary, "message": msg}, indent=2))
    if not args.no_telegram:
        send_telegram(msg, level=Level.INFO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
