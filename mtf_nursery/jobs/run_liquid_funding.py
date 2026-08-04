#!/usr/bin/env python3
"""LIQUIDCASE CNC sell-for-cash — dry-run intent with qty (C4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from calendar_ist import is_trading_day, load_market_calendar
from config import load_config
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from liquid_funding import run_liquid_funding
from notify import Level, send_telegram


def main() -> int:
    parser = argparse.ArgumentParser(description="LIQUIDCASE funding dry-run (C4)")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--token-path", default=None)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run on holiday/weekend")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    cfg["mode"] = "dry_run"
    ledger = Ledger(args.db)

    if not args.force:
        cal_dir = cfg.get("market_calendar_dir", "/home/ubuntu/fire_shop")
        try:
            if not is_trading_day(load_market_calendar(cal_dir)):
                print(json.dumps({"skipped": "holiday_or_weekend"}, indent=2))
                return 0
        except FileNotFoundError:
            pass

    try:
        kite = get_kite(token_path=args.token_path or cfg.get("kite_token_path"))
    except KiteConfigError as exc:
        send_telegram(f"Liquid funding failed: {exc}", level=Level.ERROR)
        return 1

    result = run_liquid_funding(
        ledger,
        cfg,
        kite=kite,
        send_alerts=not args.no_telegram,
    )

    payload = {
        "as_of": result.as_of.isoformat(),
        "shortfall": result.shortfall,
        "required_buffer": result.required_buffer,
        "emi_obligation": result.emi_obligation,
        "sell_amount": result.plan.sell_amount,
        "message": result.message,
        "skipped_reason": result.skipped_reason,
        "already_logged": result.already_logged,
        "exhausted": result.exhausted,
        "errors": result.errors,
    }
    if result.intent:
        payload["intent"] = {
            "symbol": result.intent.symbol,
            "qty": result.intent.qty,
            "product": result.intent.product,
            "limit_price": result.intent.limit_price,
        }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(result.message)
        if result.errors:
            for err in result.errors:
                print(f"ERROR: {err}", file=sys.stderr)

    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
