#!/usr/bin/env python3
"""Read-only broker snapshot (no ledger, no telegram)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from broker_read import build_account_snapshot
from config import load_config
from kite_client import KiteConfigError, get_kite


def main() -> int:
    root = bootstrap()
    parser = argparse.ArgumentParser(description="MTF nursery broker snapshot")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--token-path", default=None)
    args = parser.parse_args()
    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    token_path = args.token_path or cfg.get("kite_token_path") or root / ".kite_token"

    try:
        kite = get_kite(token_path=token_path)
        holdings = kite.holdings()
        margins = kite.margins()["equity"]
    except KiteConfigError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"error": f"kite api: {exc}"}), file=sys.stderr)
        return 1

    snap = build_account_snapshot(
        holdings, margins, liquid_etf_symbol=cfg.get("liquid_etf_symbol", "LIQUIDCASE")
    )
    print(
        json.dumps(
            {
                "free_cash": snap.free_cash,
                "available_cash": snap.available_cash,
                "liquid_etf_symbol": snap.liquid_etf_symbol,
                "liquid_etf_value": snap.liquid_etf_value,
                "mtf_holdings": [
                    {
                        "symbol": h.symbol,
                        "quantity": h.quantity,
                        "funded_estimate": h.funded_estimate,
                        "mtf_value": h.mtf_value,
                        "initial_margin": h.initial_margin,
                        "last_price": h.last_price,
                    }
                    for h in snap.mtf_holdings
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
