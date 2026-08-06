#!/usr/bin/env python3
"""
Weekly Genius Stock CAR check (automates Copy of Genius Stock CAR.xlsx).

For delivered (CNC) losers / watchlist symbols:
- AVOID / HOLD vs BUY / AVERAGE OUT (10 rising CA from 52w high)
- Average Out dry-run size = 1/10th original invested
- Exit when CMP ≥ avg_cost × (1 + target%):
  - 6.28% while capital < 2× original
  - 3.14% once capital has doubled
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from _bootstrap import bootstrap

root = bootstrap()

from car import CarSignal, qty_for_average_out
from car_fetch import check_symbol_car, format_car_telegram
from config import load_config
from executor import ExecMode, OrderIntent, execute_intent
from ledger import Ledger
from notify import Level, send_telegram


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly CAR Average Out check (dry-run)")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--db", default=str(root / "data" / "ledger.sqlite"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--symbol", action="append", default=None, help="Check symbol(s); repeatable")
    parser.add_argument(
        "--mark-delivered",
        metavar="SYMBOL",
        help="Mark open MTF position as delivered (CNC CAR book) then exit",
    )
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        bootstrap(args.env_file)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    cfg = load_config(cfg_path)
    car_cfg = cfg.get("car", {})
    rising = int(car_cfg.get("rising_days", 10))
    fraction = float(car_cfg.get("average_fraction", 0.10))
    profit_pct = float(car_cfg.get("profit_target_pct", 0.0628))
    profit_pct_dbl = float(car_cfg.get("profit_target_pct_when_capital_doubled", 0.0314))
    ledger = Ledger(args.db)

    if args.mark_delivered:
        sym = args.mark_delivered.upper()
        open_pos = [p for p in ledger.list_positions(status="open_mtf") if p.symbol == sym]
        if not open_pos:
            print(json.dumps({"error": f"no open_mtf position for {sym}"}))
            return 1
        ok = ledger.mark_delivered(open_pos[0].id)
        print(json.dumps({"symbol": sym, "delivered": ok}))
        return 0 if ok else 1

    today = date.today()

    # (symbol, avg_cost, original_invested, capital_deployed)
    targets: list[tuple[str, float | None, float | None, float | None]] = []
    if args.symbol:
        for sym in args.symbol:
            targets.append((sym.upper(), None, None, None))
    else:
        watch = car_cfg.get("watchlist") or []
        for sym in watch:
            targets.append((str(sym).upper(), None, None, None))
        for pos in ledger.list_car_book():
            original = pos.car_original_value if pos.car_original_value is not None else pos.buy_value
            targets.append((pos.symbol, pos.avg_price, original, pos.buy_value))

    # Dedupe by symbol (ledger wins on cost basis)
    by_sym: dict[str, tuple[str, float | None, float | None, float | None]] = {}
    for sym, avg, original, deployed in targets:
        if sym in by_sym and by_sym[sym][1] is not None:
            continue
        by_sym[sym] = (sym, avg, original, deployed)
    targets = list(by_sym.values())

    if not targets:
        msg = "CAR check: no delivered positions or watchlist symbols"
        print(msg)
        if not args.no_telegram:
            send_telegram(msg, level=Level.INFO)
        return 0

    reports = []
    for sym, avg_cost, original, deployed in targets:
        result = check_symbol_car(
            sym,
            avg_cost=avg_cost,
            original_invested=original,
            capital_deployed=deployed,
            rising_days=rising,
            average_fraction=fraction,
            profit_target_pct=profit_pct,
            profit_target_pct_doubled=profit_pct_dbl,
        )
        if result is None:
            reports.append({"symbol": sym, "error": "no_data"})
            continue

        entry = {
            "symbol": result.symbol,
            "signal": result.signal.value,
            "cmp": result.cmp,
            "avg_cost": result.avg_cost,
            "in_profit": result.in_profit,
            "profit_target_pct": result.profit_target_pct,
            "capital_doubled": result.capital_doubled,
            "average_out_amount": result.average_out_amount,
        }

        # Dry-run intents
        if result.signal == CarSignal.AVERAGE_OUT and result.average_out_amount:
            qty = qty_for_average_out(result.average_out_amount, result.cmp)
            entry["average_out_qty"] = qty
            if qty > 0:
                intent = OrderIntent(
                    side="buy",
                    symbol=sym,
                    qty=qty,
                    product="CNC",
                    reason=f"CAR Average Out 1/10th ~₹{result.average_out_amount:,.0f}",
                    limit_price=round(result.cmp * 1.001, 2),
                )
                exec_r = execute_intent(intent, mode=ExecMode.DRY_RUN.value)
                idem = f"{today}|car_avg|{sym}"
                logged = ledger.log_order_intent(
                    "buy",
                    sym,
                    qty=qty,
                    product="CNC",
                    mode="dry_run",
                    reason=intent.reason,
                    idempotency_key=idem,
                )
                # Book capital into ledger once per idempotent day (dry-run bookkeeping)
                pos = next((p for p in ledger.list_car_book() if p.symbol == sym), None)
                if pos and logged:
                    updated = ledger.apply_car_average_out(pos.id, qty, result.cmp)
                    if updated:
                        entry["capital_deployed"] = updated.buy_value
                        entry["avg_cost_after"] = updated.avg_price
                entry["buy_intent"] = exec_r.message
            else:
                entry["buy_intent"] = "skip: 1 share costs more than 1/10th budget"

        if result.in_profit and result.avg_cost is not None:
            target_label = f"{(result.profit_target_pct or profit_pct) * 100:.2f}%"
            pos = next((p for p in ledger.list_car_book() if p.symbol == sym), None)
            if pos and pos.qty > 0:
                intent = OrderIntent(
                    side="sell",
                    symbol=sym,
                    qty=pos.qty,
                    product="CNC",
                    reason=f"CAR book in profit (≥{target_label} vs avg cost)",
                    limit_price=round(result.cmp * 0.999, 2),
                )
                exec_r = execute_intent(intent, mode=ExecMode.DRY_RUN.value)
                idem = f"{today}|car_sell|{sym}"
                ledger.log_order_intent(
                    "sell",
                    sym,
                    qty=pos.qty,
                    product="CNC",
                    mode="dry_run",
                    reason=intent.reason,
                    idempotency_key=idem,
                )
                entry["sell_intent"] = exec_r.message
            else:
                entry["sell_intent"] = f"in profit (≥{target_label}) — sell manually or sync qty"

        reports.append(entry)
        tg = format_car_telegram(result)
        print(tg)
        if not args.no_telegram:
            send_telegram(tg, level=Level.INFO)

    if args.json:
        print(json.dumps({"as_of": today.isoformat(), "results": reports}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
