#!/usr/bin/env python3
"""
Algo Trader — Paper Trading Engine
Simulates live trading using real-time prices.
Saves all state to JSON. Sends identical Telegram alerts as the live bot.
No real orders placed.

Usage (cron-driven, same pattern as FIRE Shop):
  python paper_btc_buy.py     # check for buy signals (BTC)
  python paper_btc_sell.py    # check for sell/exit signals (BTC)
  python paper_gold_buy.py    # Gold Mini MCX
  python paper_gold_sell.py
  python paper_crude_buy.py
  python paper_crude_sell.py

Or directly:
  python claude_paper_trade.py --instrument btc   --mode buy
  python claude_paper_trade.py --instrument gold  --mode sell
  python claude_paper_trade.py --instrument btc   --mode status
"""

import argparse
import csv
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

from claude_regime_detector import detect_regime
from claude_strategy_engine  import RegimeDispatcher, EMAStrategy, GridStrategy, DCAStrategy

BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR / "data"
PAPER_DIR  = BASE_DIR / "paper_trades"
PAPER_DIR.mkdir(exist_ok=True)

TELEGRAM_BOT_TOKEN = os.environ.get("CRYPTO_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("CRYPTO_TELEGRAM_CHAT_ID",   "")

BINANCE_API_URL    = "https://api.binance.com/api/v3"

# ── Instrument config ─────────────────────────────────────────────────────────
#
# price_fn values:
#   "binance"  — Binance USDT price (USD) — used for BTC
#   "yahoo"    — Yahoo Finance futures (USD proxy for MCX commodities)
#
# BTC: capital is in USD ($600 ≈ ₹50,000 at ~84 rate).
#      Prices from Binance BTCUSDT. All P&L in USD terms.
#      Candle CSV (btc_1h.csv) is also USD OHLC — no currency mismatch.
#
# MCX: capital is notional INR but prices are Yahoo USD proxies.
#      Currency mismatch is known and accepted for signal validation only.

INSTRUMENT_CONFIG = {
    "btc": {
        "label":       "BTC/USDT",
        "price_fn":    "binance",
        "symbol":      "BTCUSDT",
        "capital":     600,       # USD paper capital (~₹50,000 at 84 rate)
        "lookback":    150,
        "candle_file": "btc_1h.csv",
        "currency_label": "USD",
    },
    "gold": {
        "label":       "Gold Mini MCX",
        "price_fn":    "yahoo",
        "symbol":      "GOLD",
        "capital":     50000,
        "lookback":    150,
        "candle_file": "gold_1h.csv",
        "currency_label": "USD (proxy)",
    },
    "crude": {
        "label":       "Crude Oil Mini MCX",
        "price_fn":    "yahoo",
        "symbol":      "CRUDEOIL",
        "capital":     50000,
        "lookback":    150,
        "candle_file": "crude_1h.csv",
        "currency_label": "USD (proxy)",
    },
    "silver": {
        "label":       "Silver Mini MCX",
        "price_fn":    "yahoo",
        "symbol":      "SILVER",
        "capital":     50000,
        "lookback":    150,
        "candle_file": "silver_1h.csv",
        "currency_label": "USD (proxy)",
    },
}

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        print(f"  [Telegram] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"  ⚠️  Telegram failed: {e}")

# ── State ─────────────────────────────────────────────────────────────────────

def state_file(instrument):
    return PAPER_DIR / f"claude_{instrument}_paper_state.json"

def trades_file(instrument):
    return PAPER_DIR / f"claude_{instrument}_paper_trades.json"

def load_state(instrument):
    sf = state_file(instrument)
    if not sf.exists():
        cfg = INSTRUMENT_CONFIG[instrument]
        return {
            "capital":     cfg["capital"],
            "positions":   {"ema": None, "grid": None, "dca": None},
            "trade_count": 0,
        }
    return json.loads(sf.read_text())

def save_state(instrument, state):
    state_file(instrument).write_text(json.dumps(state, indent=2))

def load_trades(instrument):
    tf = trades_file(instrument)
    if not tf.exists():
        return []
    return json.loads(tf.read_text())

def save_trade(instrument, trade):
    trades = load_trades(instrument)
    trades.append(trade)
    trades_file(instrument).write_text(json.dumps(trades, indent=2))

# ── Live price fetch ──────────────────────────────────────────────────────────


def get_live_price_binance(symbol="BTCUSDT"):
    """Binance USDT price — primary price source for BTC. Returns USD price."""
    try:
        r = requests.get(f"{BINANCE_API_URL}/ticker/price",
                         params={"symbol": symbol}, timeout=8)
        return float(r.json()["price"])
    except Exception as e:
        print(f"  ❌ Binance price fetch failed: {e}")
        return None



def get_live_price_yahoo(symbol_prefix):
    """Yahoo Finance fallback for MCX instrument live price."""
    YAHOO_MAP = {
        "GOLD":     "GC=F",   # COMEX Gold futures
        "SILVER":   "SI=F",   # COMEX Silver futures
        "CRUDEOIL": "CL=F",   # WTI Crude futures
    }
    yahoo_sym = YAHOO_MAP.get(symbol_prefix.upper())
    if not yahoo_sym:
        print(f"  ❌ No Yahoo mapping for {symbol_prefix}")
        return None
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{yahoo_sym}?interval=1m&range=1d"
        )
        r      = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        result = r.json()["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        price  = next((c for c in reversed(closes) if c), None)
        if price:
            print(f"  📍 Yahoo {yahoo_sym}: {price:.2f} (USD — proxy for MCX)")
        return float(price) if price else None
    except Exception as e:
        print(f"  ❌ Yahoo price fetch failed: {e}")
        return None


def get_live_price(instrument):
    cfg = INSTRUMENT_CONFIG[instrument]
    fn  = cfg["price_fn"]
    if fn == "binance":
        return get_live_price_binance(cfg["symbol"])
    else:
        # yahoo — MCX commodity USD proxy
        return get_live_price_yahoo(cfg["symbol"])

# ── Load recent candles ───────────────────────────────────────────────────────

def load_recent_candles(instrument, n=200):
    """Load last N candles from saved CSV for regime detection."""
    csv_path = DATA_DIR / INSTRUMENT_CONFIG[instrument]["candle_file"]
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
    candles = candles[-n:]

    # Patch last candle close with live price for freshness
    live = get_live_price(instrument)
    if live and candles:
        candles[-1]["close"] = live
        candles[-1]["high"]  = max(candles[-1]["high"], live)
        candles[-1]["low"]   = min(candles[-1]["low"],  live)

    return candles

# ── Paper order execution ─────────────────────────────────────────────────────

def paper_open(instrument, state, signal, strategy_key):
    cfg        = INSTRUMENT_CONFIG[instrument]
    cmp        = signal["price"]
    pct        = signal.get("qty_pct", 0.1)
    capital_in = state["capital"] * pct
    qty        = capital_in / cmp if cmp > 0 else 0
    sl         = signal.get("sl")
    tp         = signal.get("tp")
    side       = "long" if signal["action"] == "buy" else "short"
    ts         = datetime.now().strftime("%Y-%m-%d %H:%M")

    state["positions"][strategy_key] = {
        "side":       side,
        "entry":      cmp,
        "qty":        qty,
        "capital_in": capital_in,
        "sl":         sl,
        "tp":         tp,
        "open_ts":    ts,
    }
    state["capital"] -= capital_in
    state["trade_count"] = state.get("trade_count", 0) + 1

    msg = (
        f"📄 <b>PAPER {side.upper()} — {cfg['label']}</b>\n"
        f"Strategy : {strategy_key.upper()}\n"
        f"Entry    : {cmp:,.2f}\n"
        f"Capital  : ₹{capital_in:,.2f} ({pct*100:.0f}%)\n"
        f"SL       : {sl if sl else '—'}\n"
        f"TP       : {tp if tp else '—'}\n"
        f"Reason   : {signal.get('reason','')}\n"
        f"⏰ {ts}"
    )
    print(f"  📄 PAPER OPEN [{strategy_key}] {side} @ {cmp:.2f}  capital_in=₹{capital_in:,.0f}")
    send_telegram(msg)


def paper_close(instrument, state, signal, strategy_key):
    cfg  = INSTRUMENT_CONFIG[instrument]
    pos  = state["positions"].get(strategy_key)
    if not pos:
        return

    cmp    = signal["price"]
    entry  = pos["entry"]
    qty    = pos["qty"]
    side   = pos["side"]
    cap_in = pos["capital_in"]
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M")

    pnl     = (cmp - entry) * qty if side == "long" else (entry - cmp) * qty
    pnl_pct = pnl / cap_in * 100 if cap_in else 0

    state["capital"] += cap_in + pnl
    state["positions"][strategy_key] = None

    trade = {
        "instrument":  instrument,
        "strategy":    strategy_key,
        "side":        side,
        "open_ts":     pos["open_ts"],
        "close_ts":    ts,
        "entry":       round(entry, 4),
        "exit":        round(cmp, 4),
        "qty":         round(qty, 6),
        "capital_in":  round(cap_in, 2),
        "pnl":         round(pnl, 2),
        "pnl_pct":     round(pnl_pct, 2),
        "reason":      signal.get("reason", ""),
    }
    save_trade(instrument, trade)

    emoji = "✅" if pnl > 0 else "❌"
    msg = (
        f"{emoji} <b>PAPER CLOSE — {cfg['label']}</b>\n"
        f"Strategy : {strategy_key.upper()}\n"
        f"Side     : {side.upper()}\n"
        f"Entry    : {entry:,.2f}  →  Exit: {cmp:,.2f}\n"
        f"P&L      : ₹{pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
        f"Capital  : ₹{state['capital']:,.2f}\n"
        f"Reason   : {signal.get('reason','')}\n"
        f"⏰ {ts}"
    )
    print(f"  {emoji} PAPER CLOSE [{strategy_key}] {side} entry={entry:.2f} exit={cmp:.2f}  P&L=₹{pnl:+,.2f}")
    send_telegram(msg)

# ── Status report ─────────────────────────────────────────────────────────────

def print_status(instrument, state, regime_result, live_price):
    cfg          = INSTRUMENT_CONFIG[instrument]
    trades       = load_trades(instrument)
    total_pnl    = sum(t["pnl"] for t in trades)
    total_trades = len(trades)
    wins         = sum(1 for t in trades if t["pnl"] > 0)
    win_rate     = wins / total_trades * 100 if total_trades else 0

    # Unrealised P&L across all open positions
    unreal = 0.0
    for key, pos in state["positions"].items():
        if pos and live_price:
            if pos["side"] == "long":
                unreal += (live_price - pos["entry"]) * pos["qty"]
            else:
                unreal += (pos["entry"] - live_price) * pos["qty"]

    regime_emoji = {"trending": "📈", "ranging": "↔️", "volatile": "⚡"}.get(
        regime_result["regime"] if regime_result else "?", "?"
    )

    msg = (
        f"📊 <b>PAPER STATUS — {cfg['label']}</b>\n"
        f"Capital  : ₹{state['capital']:,.2f}\n"
        f"Unrealised: ₹{unreal:+,.2f}\n"
        f"Total P&L: ₹{total_pnl:+,.2f}\n"
        f"Trades   : {total_trades}  |  Win rate: {win_rate:.1f}%\n"
        f"Regime   : {regime_emoji} {regime_result['regime'].upper() if regime_result else 'unknown'}\n"
        f"ADX      : {regime_result['adx'] if regime_result else '—'}\n"
        f"Live CMP : {live_price:,.2f} {cfg.get('currency_label','')}\n"
        f"Open pos : {[k for k, v in state['positions'].items() if v]}\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    print(msg)
    send_telegram(msg)

# ── Main run ──────────────────────────────────────────────────────────────────

def run(instrument, mode="buy"):
    """
    mode: "buy"    — check for entry signals only
          "sell"   — check for exit signals only
          "both"   — check both (useful for testing)
          "status" — print status report
    """
    print(f"\n🔵 Paper trade — {instrument.upper()} — mode={mode}")

    candles = load_recent_candles(instrument)
    if len(candles) < 60:
        print(f"  ❌ Insufficient candles ({len(candles)}). Run data_downloader.py first.")
        return

    live_price    = get_live_price(instrument)
    state         = load_state(instrument)
    regime_result = detect_regime(candles)

    if not regime_result:
        print("  ⚠️  Regime detection failed — insufficient data")
        return

    print(f"  CMP={live_price}  Regime={regime_result['regime']}  ADX={regime_result['adx']}")

    if mode == "status":
        print_status(instrument, state, regime_result, live_price)
        return

    regime_emoji = {"trending": "📈", "ranging": "↔️", "volatile": "⚡"}.get(
        regime_result["regime"], "?"
    )

    # ── BTC: grid-only (EMA disabled — net negative on BTC in backtests) ──────
    if instrument == "btc":
        if regime_result["regime"] == "volatile":
            print(f"  ⚡ Volatile regime — grid paused")
            return

        grid = GridStrategy(grid_pct=0.015, num_levels=5, qty_per_level=0.10)
        pos  = state["positions"].get("grid")
        sig  = grid.signal(candles, regime_result, pos)

        if not sig or sig["action"] == "hold":
            print(f"  💤 No signal. Regime={regime_emoji} {regime_result['regime']}  "
                  f"ADX={regime_result['adx']:.1f}  CMP={live_price}")
            return

        action = sig["action"]
        if mode in ("buy", "both") and action == "buy" and not pos:
            paper_open(instrument, state, sig, "grid")
            save_state(instrument, state)
            return

        if mode in ("sell", "both") and action == "sell" and pos:
            paper_close(instrument, state, sig, "grid")
            save_state(instrument, state)
            return

        print(f"  💤 No signal. Regime={regime_emoji} {regime_result['regime']}  "
              f"ADX={regime_result['adx']:.1f}  CMP={live_price}")

    # ── MCX instruments: full regime dispatcher ───────────────────────────────
    else:
        dispatcher = RegimeDispatcher(
            ema_strategy  = EMAStrategy(
                ema_fast=50, ema_slow=200, adx_min=30,
                sl_atr_mult=2.5, tp_atr_mult=5.0, allow_short=True
            ),
            grid_strategy = GridStrategy(grid_pct=0.015, num_levels=5, qty_per_level=0.10),
            dca_strategy  = DCAStrategy(dip_pct=0.03, target_pct=0.06, qty_pct=0.15),
        )
        signals = dispatcher.signal(candles, regime_result, state["positions"])

        for key, sig in signals.items():
            if not sig:
                continue
            action = sig.get("action", "hold")
            pos    = state["positions"].get(key)

            if mode in ("buy", "both") and action in ("buy", "short") and pos is None:
                paper_open(instrument, state, sig, key)
                save_state(instrument, state)
                return

            if mode in ("sell", "both") and action in ("sell", "buy") and pos is not None:
                if (action == "sell" and pos["side"] == "long") or \
                   (action == "buy"  and pos["side"] == "short"):
                    paper_close(instrument, state, sig, key)
                    save_state(instrument, state)
                    return

        print(f"  💤 No signal. Regime={regime_emoji} {regime_result['regime']}  "
              f"ADX={regime_result['adx']:.1f}  CMP={live_price}")


def main():
    parser = argparse.ArgumentParser(description="Algo Trader — Paper Trading Engine")
    parser.add_argument("--instrument", required=True,
                        choices=list(INSTRUMENT_CONFIG.keys()))
    parser.add_argument("--mode", default="buy",
                        choices=["buy", "sell", "both", "status"])
    args = parser.parse_args()
    run(args.instrument, args.mode)


if __name__ == "__main__":
    main()
