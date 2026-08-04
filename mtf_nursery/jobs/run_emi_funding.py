#!/usr/bin/env python3
"""Morning EMI check + LIQUIDCASE funding intent (dry-run)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from config import load_config
from dry_run import run_dry_cycle
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from notify import Level, send_telegram


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--token-path", default=None)
    parser.add_argument("--sync-mtf", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    ledger = Ledger(args.db)

    try:
        kite = get_kite(token_path=args.token_path or cfg.get("kite_token_path"))
    except KiteConfigError as exc:
        send_telegram(f"EMI job failed: {exc}", level=Level.ERROR)
        return 1

    if args.sync_mtf:
        from broker_read import build_account_snapshot

        snap = build_account_snapshot(
            kite.holdings(),
            kite.margins()["equity"],
            liquid_etf_symbol=cfg.get("liquid_etf_symbol", "LIQUIDCASE"),
        )
        for h in snap.mtf_holdings:
            ledger.import_mtf_from_broker(
                h.symbol, h.quantity, h.average_price, h.initial_margin, date.today()
            )

    report = run_dry_cycle(ledger, cfg, kite=kite, send_alerts=True)
    if report.liquid_topup_intent:
        print(report.liquid_topup_intent)
    for msg in report.emi_alerts_sent:
        print("EMI:", msg)
    for msg in report.emi_verified:
        print(msg)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
