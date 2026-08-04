#!/usr/bin/env python3
"""MTF buy (≤1/day) from top scan candidate — dry-run or live (C6)."""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from _execution import run_order_with_ledger
from broker_read import build_account_snapshot
from calendar_ist import is_trading_day, load_market_calendar
from config import load_config
from dry_run import _load_top_scan_candidate
from executor import OrderIntent, format_gate_block
from kite_client import KiteConfigError, get_kite
from ledger import Ledger
from live_trading import mtf_live_enabled
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
    ledger = Ledger(args.db)
    ledger.ensure_step(1, cfg.get("ticket_start", 15000))
    today = date.today()

    cal_dir = cfg.get("market_calendar_dir", "/home/ubuntu/fire_shop")
    try:
        cal = load_market_calendar(cal_dir)
        if not is_trading_day(cal, today):
            print("Market closed — skip buy")
            return 0
    except FileNotFoundError:
        pass

    symbol = _load_top_scan_candidate(cfg)
    if not symbol:
        send_telegram("Buy skip: no scan candidates (run scan first).", level=Level.INFO)
        return 0

    try:
        kite = get_kite(token_path=args.token_path or cfg.get("kite_token_path"))
        holdings = kite.holdings()
        margins = kite.margins()["equity"]
    except KiteConfigError as exc:
        send_telegram(f"Buy job failed: {exc}", level=Level.ERROR)
        return 1

    snap = build_account_snapshot(
        holdings, margins, liquid_etf_symbol=cfg.get("liquid_etf_symbol", "LIQUIDCASE")
    )
    ticket = ledger.current_ticket(cfg.get("ticket_start", 15000))
    est_margin = ticket * 0.30
    buffer = ticket * float(cfg.get("buffer_pct", 0.10))
    immediate = est_margin + buffer

    gate = ledger.evaluate_buy(
        free_cash=snap.free_cash,
        ticket_immediate_need=immediate,
        as_of=today,
        fire_shop_reserve=float(cfg.get("fire_shop_daily_reserve", 6000)),
        max_buys_per_day=int(cfg.get("max_buys_per_day", 1)),
        max_mtf_buys_per_month=int(cfg.get("max_mtf_buys_per_month", 2)),
        buy_blocked_by_rms=False,
    )

    if not gate.allowed:
        msg = format_gate_block("BUY", symbol, gate)
        send_telegram(msg, level=Level.INFO)
        print(msg)
        return 0

    cmp = _cmp_for_symbol(symbol, holdings, cfg)
    if cmp <= 0:
        send_telegram(f"Buy skip {symbol}: no price", level=Level.WARN)
        return 0

    qty = max(1, int(math.floor(ticket / cmp)))
    intent = OrderIntent(
        side="buy",
        symbol=symbol,
        qty=qty,
        product="MTF",
        reason=f"top D1=A scan ticket ~{ticket}",
        limit_price=round(cmp * 1.001, 2),
    )
    idem = f"{today}|buy|{symbol}"

    try:
        result, msg, already = run_order_with_ledger(
            ledger,
            intent,
            cfg=cfg,
            idempotency_key=idem,
            kite=kite,
            gate_results=gate,
        )
    except Exception as exc:
        send_telegram(f"Buy failed {symbol}: {exc}", level=Level.ERROR)
        return 1

    if result and result.executed and mtf_live_enabled(cfg):
        buy_value = round(qty * cmp, 2)
        initial_margin = round(buy_value * 0.30, 2)
        ledger.add_position(
            symbol,
            today,
            qty,
            cmp,
            initial_margin,
            step_id=1,
            buffer_pct=float(cfg.get("buffer_pct", 0.10)),
            emi_weeks=int(cfg.get("emi_weeks", 16)),
        )

    level = Level.CRITICAL if result and result.executed else Level.INFO
    if not already:
        send_telegram(f"{msg} qty={qty} ~₹{qty * cmp:,.0f}", level=level)
    print(msg)
    return 0


def _cmp_for_symbol(symbol: str, holdings: list, cfg: dict) -> float:
    from scanner_fetch import load_scan_result

    for h in holdings:
        if h.get("tradingsymbol") == symbol:
            return float(h.get("last_price") or 0)
    path = Path(cfg.get("scan_output", "data/last_scan.json"))
    if not path.is_absolute():
        path = root / path
    if path.exists():
        data = load_scan_result(path)
        for c in data.get("candidates", []):
            if c.get("symbol") == symbol:
                return float(c.get("cmp") or 0)
    return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
