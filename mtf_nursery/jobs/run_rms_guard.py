#!/usr/bin/env python3
"""RMS guard — cash/MTM check, block signal (dry-run)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from config import load_config
from dry_run import run_dry_cycle
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from notify import Level, send_telegram
from rms_guard import RmsSeverity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--token-path", default=None)
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
        send_telegram(f"RMS job failed: {exc}", level=Level.ERROR)
        return 1

    report = run_dry_cycle(ledger, cfg, kite=kite, send_alerts=False)
    if report.errors:
        send_telegram(f"RMS job errors: {report.errors}", level=Level.ERROR)
        return 1

    level = Level.INFO
    if report.rms_severity == RmsSeverity.WARN.value:
        level = Level.WARN
    elif report.rms_severity == RmsSeverity.CRITICAL.value:
        level = Level.CRITICAL

    msg = f"RMS {report.rms_severity}"
    if report.rms_messages:
        msg += ": " + "; ".join(report.rms_messages)
    if report.liquid_topup_intent:
        msg += f" | {report.liquid_topup_intent}"
    send_telegram(msg, level=level)
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
