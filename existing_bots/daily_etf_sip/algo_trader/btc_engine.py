#!/usr/bin/env python3
"""
Algo Trader — BTC Futures Live Engine (Delta Exchange India)
Mirrors FIRE Shop engine.py structure exactly.

One action per run. Called by cron via btc_buy_engine.py / btc_sell_engine.py.
State persisted in btc_state.json.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from delta_adapter   import (get_cmp, get_position, get_balance,
                              place_order, place_stop_loss, calc_qty,
                              send_telegram, DELTA_API_KEY)
from regime_detector import detect_regime
from strategy_engine  import RegimeDispatcher, EMAStrategy, GridStrategy, DCAStrategy

BASE_DIR   = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "btc_state.json"
DATA_DIR   = BASE_DIR / "data"

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG = {
    "symbol":          "BTCUSD",
    "capital_inr":     50000,     # INR allocated to BTC bot
    "leverage":        1,         # 1x — no amplification
    "lookback":        150,       # candles for regime detection
    "candle_file":     "btc_1h.csv",
    "limit_buffer":    0.001,     # 0.1% limit price buffer (same as FIRE Shop)
}

# ── State ─────────────────────────────────────────────────────────────────────

def load_state():
    if not STATE_FILE.exists():
        return {
            "positions": {"ema": None, "grid": None, "dca": None},
            "orders":    {},
            "last_run":  None,
        }
    return json.loads(STATE_FILE.read_text())

def save_state(state):
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Candles ───────────────────────────────────────────────────────────────────

def load_candles():
    import csv
    csv_path = DATA_DIR / CONFIG["candle_file"]
    if not csv_path.exists():
        return []
    candles = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                candles.append({
                    "ts":     row["ts"],
                    "open":   float(row["open"]),
                    "high":   float(row["high"]),
                    "low":    float(row["low"]),
                    "close":  float(row["close"]),
                    "volume": float(row["volume"]),
                })
            except (ValueError, KeyError):
                continue
    candles = candles[-CONFIG["lookback"]:]

    # Patch with live price
    cmp = get_cmp(CONFIG["symbol"])
    if cmp and candles:
        candles[-1]["close"] = cmp
        candles[-1]["high"]  = max(candles[-1]["high"], cmp)
        candles[-1]["low"]   = min(candles[-1]["low"], cmp)
    return candles

# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_sell=True, run_buy=True):
    print(f"🚀 BTC engine — sell={run_sell} buy={run_buy}")

    state   = load_state()
    candles = load_candles()
    if len(candles) < 60:
        print("❌ Insufficient candles. Run data_downloader.py --instrument btc")
        return

    cmp = get_cmp(CONFIG["symbol"])
    if not cmp:
        print("❌ Could not fetch live price")
        return

    print(f"  CMP: {cmp:,.2f}")

    regime_result = detect_regime(candles)
    if not regime_result:
        print("  ⚠️  Regime detection failed")
        return

    print(f"  Regime: {regime_result['regime']}  ADX={regime_result['adx']:.1f}  "
          f"Direction={regime_result['direction']}")

    dispatcher = RegimeDispatcher(
        ema_strategy  = EMAStrategy(ema_fast=20, ema_slow=50, adx_min=22,
                                     sl_atr_mult=1.5, tp_atr_mult=3.0, allow_short=True),
        grid_strategy = GridStrategy(grid_pct=0.015, num_levels=5, qty_per_level=0.10),
        dca_strategy  = DCAStrategy(dip_pct=0.03, target_pct=0.06, qty_pct=0.15),
    )

    signals = dispatcher.signal(candles, regime_result, state["positions"])

    # ── SELL / CLOSE ──────────────────────────────────────────────────────────
    if run_sell:
        for key, sig in signals.items():
            if not sig:
                continue
            pos    = state["positions"].get(key)
            action = sig.get("action", "hold")

            should_close = (
                pos is not None and (
                    (action == "sell" and pos["side"] == "long") or
                    (action == "buy"  and pos["side"] == "short")
                )
            )

            if should_close:
                close_side = "sell" if pos["side"] == "long" else "buy"
                qty        = int(pos.get("qty", 0))
                if qty <= 0:
                    continue

                print(f"  🔴 CLOSE [{key}] {close_side} {qty} contracts @ {cmp:.2f}")
                oid, _ = place_order(
                    symbol     = CONFIG["symbol"],
                    side       = close_side,
                    qty        = qty,
                    order_type = "market_order",
                    reduce_only= True,
                    label      = f"{key} close"
                )

                if oid:
                    entry    = pos.get("entry", cmp)
                    cap_in   = pos.get("capital_in", 0)
                    pnl_est  = (cmp - entry) * qty if pos["side"] == "long" else (entry - cmp) * qty
                    pnl_pct  = pnl_est / cap_in * 100 if cap_in else 0
                    emoji    = "✅" if pnl_est > 0 else "❌"

                    send_telegram(
                        f"{emoji} <b>CLOSED [{key.upper()}] {CONFIG['symbol']}</b>\n"
                        f"Side     : {pos['side'].upper()}\n"
                        f"Entry    : {entry:,.2f}  Exit: {cmp:,.2f}\n"
                        f"Est. P&L : ₹{pnl_est:+,.2f} ({pnl_pct:+.2f}%)\n"
                        f"Reason   : {sig.get('reason','')}"
                    )
                    state["positions"][key] = None
                    save_state(state)
                    return

    # ── BUY / OPEN ────────────────────────────────────────────────────────────
    if run_buy:
        for key, sig in signals.items():
            if not sig:
                continue
            pos    = state["positions"].get(key)
            action = sig.get("action", "hold")

            if pos is not None:
                continue   # already have a position in this strategy slot

            if action not in ("buy", "short") or sig.get("qty_pct", 0) <= 0:
                continue

            side     = "buy" if action == "buy" else "sell"
            qty      = calc_qty(
                CONFIG["capital_inr"] * sig["qty_pct"],
                cmp,
                CONFIG["leverage"]
            )
            if qty <= 0:
                continue

            # Limit price with buffer
            if side == "buy":
                price = round(cmp * (1 + CONFIG["limit_buffer"]), 1)
            else:
                price = round(cmp * (1 - CONFIG["limit_buffer"]), 1)

            print(f"  🟢 OPEN [{key}] {side} {qty} contracts @ {price:.2f}")
            oid, _ = place_order(
                symbol     = CONFIG["symbol"],
                side       = side,
                qty        = qty,
                order_type = "limit_order",
                price      = price,
                label      = f"{key} entry"
            )

            if oid:
                sl = sig.get("sl")
                tp = sig.get("tp")

                # Place stop loss
                if sl:
                    sl_side = "sell" if side == "buy" else "buy"
                    place_stop_loss(CONFIG["symbol"], sl_side, qty, sl)

                state["positions"][key] = {
                    "side":       "long" if side == "buy" else "short",
                    "entry":      price,
                    "qty":        qty,
                    "capital_in": CONFIG["capital_inr"] * sig["qty_pct"],
                    "sl":         sl,
                    "tp":         tp,
                    "order_id":   oid,
                    "open_ts":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "strategy":   key,
                    "regime":     regime_result["regime"],
                }

                send_telegram(
                    f"🟢 <b>OPENED [{key.upper()}] {CONFIG['symbol']}</b>\n"
                    f"Side     : {side.upper()}\n"
                    f"Entry    : {price:,.2f}\n"
                    f"Qty      : {qty} contracts\n"
                    f"SL       : {sl if sl else '—'}\n"
                    f"TP       : {tp if tp else '—'}\n"
                    f"Regime   : {regime_result['regime']}  ADX={regime_result['adx']:.1f}\n"
                    f"Reason   : {sig.get('reason','')}"
                )
                save_state(state)
                return

    print("  💤 No action taken.")


if __name__ == "__main__":
    main()
