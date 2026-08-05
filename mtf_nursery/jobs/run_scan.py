#!/usr/bin/env python3
"""Scan FO universe (D1=A) via yfinance; save data/last_scan.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from config import load_config
from calendar_ist import should_run_scan
from notify import Level, send_telegram
from scanner import ScanConfig
from scanner_fetch import (
    candidates_to_dict,
    load_universe,
    save_scan_result,
    scan_universe,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF nursery FO scanner (D1=A)")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument(
        "--output",
        default=str(root / "data" / "last_scan.json"),
    )
    parser.add_argument("--universe", default=None, help="Path to fo_universe.json")
    parser.add_argument("--limit", type=int, default=None, help="Scan first N only (testing)")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--env-file", default=None, help="e.g. /home/ubuntu/.env_fire_shop")
    parser.add_argument("--token-path", default=None, help="ignored; accepted for cron wrapper")
    parser.add_argument("--force", action="store_true", help="Run even on holiday/weekend")
    args = parser.parse_args()

    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)

    if not args.force and not should_run_scan(cfg):
        out = {"skipped": "holiday_or_weekend"}
        print(json.dumps(out, indent=2))
        return 0

    sc = cfg.get("scanner", {})
    scan_cfg = ScanConfig(
        require_dma30_gt_dma50=sc.get("require_dma30_gt_dma50", True),
        max_dist_200_pct=float(sc.get("max_dist_200_pct", 10.0)),
        car_rising_days=int(sc.get("car_rising_days", 10)),
    )

    symbols = load_universe(args.universe)
    print(f"Scanning {len(symbols) if not args.limit else args.limit} symbols (D1=A)...", file=sys.stderr)

    candidates = scan_universe(symbols, scan_cfg, limit=args.limit)
    save_scan_result(candidates, args.output)

    payload = candidates_to_dict(candidates)
    print(json.dumps(payload, indent=2))

    if candidates and not args.no_telegram:
        top = candidates[0]
        send_telegram(
            f"Scan {payload['scan_date']}: {len(candidates)} candidates. "
            f"Top: {top.symbol} CMP {top.cmp} dist200 {top.dist_200_pct:.2f}%",
            level=Level.INFO,
        )
    elif not candidates and not args.no_telegram:
        send_telegram(f"Scan {payload['scan_date']}: no D1=A candidates today.", level=Level.INFO)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
