#!/usr/bin/env python3
"""Print ledger status summary (C1 — no broker)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from config import load_config
from ledger import Ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="MTF nursery ledger status")
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent.parent / "data" / "ledger.sqlite"),
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    parser.add_argument(
        "--init-step",
        action="store_true",
        help="Ensure step 1 exists with ticket from config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    ledger = Ledger(args.db)

    if args.init_step:
        ledger.ensure_step(1, cfg["ticket_start"])

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    summary = ledger.status_summary(as_of)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
