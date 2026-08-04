#!/usr/bin/env python3
"""Dry-run sell intent — highest MTF winner ≥6.28% (≤1/day)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from broker_read import parse_holdings
from calendar_ist import is_trading_day, load_market_calendar
from compounding import OpenPosition, pick_sell_candidate
from config import load_config
from executor import ExecMode, OrderIntent, execute_intent, format_gate_block
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from notify import Level, send_telegram


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
    cfg["mode"] = ExecMode.DRY_RUN.value
    ledger = Ledger(args.db)
    today = date.today()
    target_pct = float(cfg.get("profit_target_pct", 0.0628))

    cal_dir = cfg.get("market_calendar_dir", "/home/ubuntu/fire_shop")
    try:
        if not is_trading_day(load_market_calendar(cal_dir), today):
            print("Market closed — skip sell")
            return 0
    except FileNotFoundError:
        pass

    try:
        kite = get_kite(token_path=args.token_path or cfg.get("kite_token_path"))
        holdings = kite.holdings()
    except KiteConfigError as exc:
        send_telegram(f"Sell job failed: {exc}", level=Level.ERROR)
        return 1

    positions: list[OpenPosition] = []
    for mtf in parse_holdings(holdings):
        buy_value = mtf.mtf_value
        current = mtf.last_price * mtf.quantity
        positions.append(OpenPosition(mtf.symbol, buy_value, current))

    winner = pick_sell_candidate(positions, target_pct)
    gate = ledger.evaluate_sell(
        has_eligible_winner=winner is not None,
        as_of=today,
        max_sells_per_day=int(cfg.get("max_sells_per_day", 1)),
    )

    if not gate.allowed or winner is None:
        msg = "Sell skip: no eligible winner" if winner is None else format_gate_block(
            "SELL", winner.symbol, gate
        )
        print(msg)
        return 0

    mtf = next(m for m in parse_holdings(holdings) if m.symbol == winner.symbol)
    intent = OrderIntent(
        side="sell",
        symbol=winner.symbol,
        qty=mtf.quantity,
        product="MTF",
        reason=f"winner ≥{target_pct*100:.2f}%",
        limit_price=round(mtf.last_price * 0.999, 2),
    )
    result = execute_intent(intent, mode=cfg["mode"])
    idem = f"{today}|sell|{winner.symbol}"
    ledger.log_order_intent(
        "sell",
        winner.symbol,
        qty=mtf.quantity,
        product="MTF",
        mode=cfg["mode"],
        reason=intent.reason,
        idempotency_key=idem,
    )
    msg = result.message
    send_telegram(msg, level=Level.INFO)
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
