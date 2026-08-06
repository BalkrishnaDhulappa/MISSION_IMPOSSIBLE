#!/usr/bin/env python3
"""
Algo Trader — BTC Futures Live Engine (Delta Exchange India)
Mirrors FIRE Shop engine.py structure exactly.

Strategy: Grid-only (EMA disabled — consistently underperforms on BTC).
Grid captures BTC's natural oscillations in both ranging and trending regimes.

One action per run. Called by cron via claude_btc_buy_engine.py / claude_btc_sell_engine.py.
State persisted in claude_btc_state.json.
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from claude_delta_adapter   import (get_cmp, get_position, get_balance,
                                     place_order, place_stop_loss, calc_qty,
                                     send_telegram, DELTA_API_KEY)
from claude_regime_detector import detect_regime
from claude_strategy_engine  import GridStrategy

BASE_DIR   = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "claude_btc_state.json"
DATA_DIR   = BASE_DIR.parent / "data"

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG = {
    "symbol":          "BTCUSD",
    "capital_inr":     50000,
    "leverage":        1,
    "lookback":        150,
    "candle_file":     "btc_1h.csv",
    "limit_buffer":    0.001,
    # Grid parameters — tuned from backtest
    "grid_pct":        0.015,     # 1.5% between levels
    "num_levels":      5,
    "qty_per_level":   0.10,      # 10% of capital per level
}

# ── State ─────────────────────────────────────────────────────────────────────

def load_state():
    if not STATE_FILE.exists():
        return {"position": None, "last_run": None}
    return json.loads(STATE_FILE.read_text())

def save_state(state):
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Candles ───────────────────────────────────────────────────────────────────

def load_candles():
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

    # Patch last candle with live price
    cmp = get_cmp(CONFIG["symbol"])
    if cmp and candles:
        candles[-1]["close"] = cmp
        candles[-1]["high"]  = max(candles[-1]["high"], cmp)
        candles[-1]["low"]   = min(candles[-1]["low"],  cmp)
    return candles

# ── Main ──────────────────────────────────────────────────────────────────────

def main(run_sell=True, run_buy=True):
    print(f"🚀 BTC engine — sell={run_sell} buy={run_buy}")

    state   = load_state()
    candles = load_candles()

    if len(candles) < 60:
        print("❌ Insufficient candles. Run claude_data_downloader.py --instrument btc")
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

    regime  = regime_result["regime"]
    adx     = regime_result["adx"]
    print(f"  Regime: {regime}  ADX={adx:.1f}  Direction={regime_result['direction']}")

    # Pause completely during volatile regime — protect capital
    if regime == "volatile":
        print("  ⚡ Volatile regime — no action")
        send_telegram(
            f"⚡ <b>BTC — Volatile regime detected</b>\n"
            f"ADX={adx:.1f}  ATR ratio={regime_result['atr_ratio']:.2f}x\n"
            f"Grid paused. No new orders."
        )
        return

    grid = GridStrategy(
        grid_pct      = CONFIG["grid_pct"],
        num_levels    = CONFIG["num_levels"],
        qty_per_level = CONFIG["qty_per_level"],
    )

    pos = state.get("position")
    sig = grid.signal(candles, regime_result, pos)

    if not sig or sig["action"] == "hold":
        print(f"  💤 No signal — {sig.get('reason','') if sig else 'none'}")
        return

    action = sig["action"]

    # ── SELL / CLOSE ──────────────────────────────────────────────────────────
    if run_sell and action == "sell" and pos:
        qty = int(pos.get("qty", 0))
        if qty <= 0:
            return

        print(f"  🔴 CLOSE grid long {qty} contracts @ {cmp:.2f}")
        oid, _ = place_order(
            symbol      = CONFIG["symbol"],
            side        = "sell",
            qty         = qty,
            order_type  = "market_order",
            reduce_only = True,
            label       = "grid close"
        )

        if oid:
            entry   = pos.get("entry", cmp)
            cap_in  = pos.get("capital_in", 0)
            pnl_est = (cmp - entry) * qty
            pnl_pct = pnl_est / cap_in * 100 if cap_in else 0
            emoji   = "✅" if pnl_est > 0 else "❌"

            send_telegram(
                f"{emoji} <b>GRID CLOSED — {CONFIG['symbol']}</b>\n"
                f"Entry    : {entry:,.2f}  →  Exit: {cmp:,.2f}\n"
                f"Est. P&L : ₹{pnl_est:+,.2f} ({pnl_pct:+.2f}%)\n"
                f"Regime   : {regime}  ADX={adx:.1f}\n"
                f"Reason   : {sig.get('reason','')}"
            )
            state["position"] = None
            save_state(state)
        return

    # ── BUY / OPEN ────────────────────────────────────────────────────────────
    if run_buy and action == "buy" and not pos:
        qty = calc_qty(
            CONFIG["capital_inr"] * sig["qty_pct"],
            cmp,
            CONFIG["leverage"]
        )
        if qty <= 0:
            return

        price = round(cmp * (1 + CONFIG["limit_buffer"]), 1)

        print(f"  🟢 OPEN grid long {qty} contracts @ {price:.2f}")
        oid, _ = place_order(
            symbol     = CONFIG["symbol"],
            side       = "buy",
            qty        = qty,
            order_type = "limit_order",
            price      = price,
            label      = "grid entry"
        )

        if oid:
            sl = sig.get("sl")
            tp = sig.get("tp")

            if sl:
                place_stop_loss(CONFIG["symbol"], "sell", qty, sl)

            state["position"] = {
                "side":       "long",
                "entry":      price,
                "qty":        qty,
                "capital_in": CONFIG["capital_inr"] * sig["qty_pct"],
                "sl":         sl,
                "tp":         tp,
                "order_id":   oid,
                "open_ts":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                "regime":     regime,
            }

            send_telegram(
                f"🟢 <b>GRID OPENED — {CONFIG['symbol']}</b>\n"
                f"Entry    : {price:,.2f}\n"
                f"Qty      : {qty} contracts\n"
                f"SL       : {sl if sl else '—'}\n"
                f"TP       : {tp if tp else '—'}\n"
                f"Regime   : {regime}  ADX={adx:.1f}\n"
                f"Reason   : {sig.get('reason','')}"
            )
            save_state(state)
        return

    print("  💤 No action taken.")


if __name__ == "__main__":
    main()
