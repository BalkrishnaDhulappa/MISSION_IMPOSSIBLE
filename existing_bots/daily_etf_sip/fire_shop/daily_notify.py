#!/usr/bin/env python3
"""
daily_notify.py — Daily Strategy Notification System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Monitors 3 strategy layers and sends a single Telegram message every morning.

LAYER 1 — Equity (always):
  Golden Cross  → Nifty 50
  BB Squeeze    → Bajaj Finserv, SBI, Grasim, UltraCemco

LAYER 2 — F&O Base (every week):
  Iron Condor   → Nifty weekly options
  Strategy selected by VIX × Regime matrix

LAYER 3 — F&O Kicker (conditional):
  Nifty Futures → Long or Short when 2/3 signals align
  Roll Wednesday if signal still active
  1% stop loss. No weekend gap exposure.

Cron (IST 9:30 AM weekdays):
  30 9 * * 1-5 /home/ubuntu/fire_shop/venv/bin/python3 \
    /home/ubuntu/fire_shop/daily_notify.py >> /home/ubuntu/fire_shop/logs/notify.log 2>&1

Usage:
  python daily_notify.py            # run normally
  python daily_notify.py --test     # print to terminal only
  python daily_notify.py --force    # run on weekends too
"""

import math
import sys
import json
import os
import argparse
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf
import requests

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

NIFTY_TICKER = "^NSEI"
VIX_TICKER   = "^INDIAVIX"
DATA_DAYS    = 400

# Iron Condor parameters
IC_SHORT_OTM = 0.01     # sell strikes 1% OTM
IC_WING_OTM  = 0.02     # buy wings 2% OTM
NIFTY_STRIKE_STEP = 50  # Nifty strikes in multiples of 50

# Futures parameters
FUT_SL_PCT   = 0.01     # 1% stop loss on futures leg
FUT_LOT_SIZE = 65       # current Nifty lot size (Jan 2026)

# Capital reference (for position sizing display)
CAPITAL = 500_000       # ₹5,00,000

# State file — tracks open futures position across days
STATE_FILE = Path(__file__).parent / "fo_state.json"

# BB Squeeze stocks
BB_STOCKS = {
    "Bajaj Finserv": "BAJAJFINSV.NS",
    "SBI":           "SBIN.NS",
    "Grasim":        "GRASIM.NS",
    "UltraCemco":    "ULTRACEMCO.NS",
}
REGIME_FILTER_STOCKS = {"SBI", "UltraCemco"}

# GFS Screener — Nifty 500 cache location
GFS_SCREENER_CACHE = Path(__file__).parent / "data_cache" / "nifty500_list.csv"
NSE_N500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
NSE_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.niftyindices.com/",
}

# GFS thresholds — long setup
GFS_LONG_M_MIN  = 60    # Monthly RSI > 60
GFS_LONG_W_MIN  = 60    # Weekly  RSI > 60
GFS_LONG_D_MIN  = 35    # Daily   RSI >= 35
GFS_LONG_D_MAX  = 45    # Daily   RSI <  45

# GFS thresholds — short/inverse setup
GFS_SHORT_M_MAX = 40    # Monthly RSI < 40
GFS_SHORT_W_MAX = 40    # Weekly  RSI < 40
GFS_SHORT_D_MIN = 55    # Daily   RSI >= 55
GFS_SHORT_D_MAX = 65    # Daily   RSI <  65

# Max stocks to show per GFS section in Telegram (keep message short)
GFS_MAX_DISPLAY = 6

# Indicator parameters
BB_PERIOD   = 20
BB_STD      = 2
RSI_PERIOD  = 14
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIG    = 9

# Known NSE events to skip (add dates manually as needed)
NSE_EVENT_DATES = {
    # Format: date(YYYY, MM, DD): "Event name"
    # date(2026, 4, 1): "RBI Policy",
    # date(2026, 2, 1): "Union Budget",
}

# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT — persists futures position across days
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_STATE = {
    "futures_position": None,   # None or dict with position details
    "ic_position":      None,   # None or dict with current week IC details
    "last_updated":     "",
}

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            for k, v in DEFAULT_STATE.items():
                s.setdefault(k, v)
            return s
        except Exception:
            pass
    return dict(DEFAULT_STATE)

def save_state(state: dict):
    state["last_updated"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────
def fetch(ticker: str, days: int = DATA_DAYS) -> pd.DataFrame | None:
    try:
        raw = yf.download(ticker, period=f"{days}d",
                          progress=False, auto_adjust=True)
        if raw is None or len(raw) < 60:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index = pd.to_datetime(df.index)
        return df.dropna()
    except Exception as e:
        print(f"  fetch error {ticker}: {e}")
        return None

def fetch_vix() -> float | None:
    try:
        raw = yf.download(VIX_TICKER, period="5d", progress=False)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return float(raw["Close"].iloc[-1])
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]

    # SMAs
    for p in [5, 10, 20, 50, 100, 200]:
        df[f"sma{p}"] = c.rolling(p).mean()

    # EMAs + MACD
    ema_fast          = c.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow          = c.ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"]        = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIG, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # RSI
    delta        = c.diff()
    gain         = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss         = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    df["rsi"]    = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    # Bollinger Bands
    bb_mid         = c.rolling(BB_PERIOD).mean()
    bb_std         = c.rolling(BB_PERIOD).std()
    df["bb_upper"] = bb_mid + BB_STD * bb_std
    df["bb_lower"] = bb_mid - BB_STD * bb_std
    df["bb_mid"]   = bb_mid
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid * 100

    return df.dropna(subset=["sma200", "rsi", "macd"])

# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def get_regime(row: pd.Series) -> str:
    price  = float(row["close"])
    sma50  = float(row["sma50"])
    sma200 = float(row["sma200"])
    if price > sma200 and sma50 > sma200:
        return "Bull"
    if price < sma200 and sma50 < sma200:
        return "Bear"
    return "Sideways"

# ─────────────────────────────────────────────────────────────────────────────
# VIX ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
def classify_vix(vix: float) -> dict:
    if vix > 25:
        return {"zone": "EXTREME FEAR",  "emoji": "🔴", "color": "red",    "sell_premium": True,  "size": 0.5}
    elif vix > 20:
        return {"zone": "HIGH",          "emoji": "🟠", "color": "orange", "sell_premium": True,  "size": 1.0}
    elif vix > 16:
        return {"zone": "ELEVATED",      "emoji": "🟡", "color": "yellow", "sell_premium": True,  "size": 1.0}
    elif vix > 12:
        return {"zone": "NORMAL",        "emoji": "🟢", "color": "green",  "sell_premium": True,  "size": 0.5}
    else:
        return {"zone": "COMPLACENCY",   "emoji": "⚪", "color": "gray",   "sell_premium": False, "size": 0.0}

def get_vix_20d_avg(vix_series: pd.Series) -> float:
    return float(vix_series.rolling(20).mean().iloc[-1])

# ─────────────────────────────────────────────────────────────────────────────
# IRON CONDOR STRIKE CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────
def round_strike(price: float, step: int = NIFTY_STRIKE_STEP) -> int:
    return int(round(price / step) * step)

def calc_ic_strikes(spot: float, vix: float, regime: str) -> dict:
    """
    Calculate Iron Condor strikes based on VIX and regime.
    Higher VIX or trending market → wider strikes for safety.
    """
    # Base OTM distance from VIX
    if vix > 20:
        short_pct = 0.015   # 1.5% OTM — wider in high vol
        wing_pct  = 0.025   # 2.5% wing
    elif vix > 16:
        short_pct = 0.012
        wing_pct  = 0.022
    else:
        short_pct = 0.010   # standard 1%
        wing_pct  = 0.020   # standard 2%

    # In trending markets widen the trend side for extra safety
    call_short_pct = short_pct
    put_short_pct  = short_pct

    if regime == "Bull":
        call_short_pct *= 1.3    # widen call side in bull — upside risk
    elif regime == "Bear":
        put_short_pct  *= 1.3    # widen put side in bear — downside risk

    sell_ce = round_strike(spot * (1 + call_short_pct))
    buy_ce  = round_strike(spot * (1 + wing_pct))
    sell_pe = round_strike(spot * (1 - put_short_pct))
    buy_pe  = round_strike(spot * (1 - wing_pct))

    spread_ce = buy_ce - sell_ce
    spread_pe = sell_pe - buy_pe
    max_loss  = max(spread_ce, spread_pe)   # per unit

    # Approximate premium (rough BS-less estimate based on VIX and DTE)
    # Real premium checked at market — this is for notification context
    weekly_iv    = (vix / 100) / math.sqrt(52)
    approx_prem_ce = max(5, int(spot * weekly_iv * 0.4))   # rough ATM approximation
    approx_prem_pe = max(5, int(spot * weekly_iv * 0.4))
    wing_cost      = max(3, int(approx_prem_ce * 0.3))
    net_premium    = max(10, (approx_prem_ce + approx_prem_pe) - (wing_cost * 2))

    return {
        "sell_ce":    sell_ce,
        "buy_ce":     buy_ce,
        "sell_pe":    sell_pe,
        "buy_pe":     buy_pe,
        "spread":     max_loss,
        "net_premium": net_premium,
        "max_loss_per_lot": max_loss * FUT_LOT_SIZE,
        "net_per_lot": net_premium * FUT_LOT_SIZE,
    }

def get_ic_strategy(vix: float, regime: str, vix_above_avg: bool) -> dict:
    """
    Returns recommended IC strategy and sizing based on VIX × regime matrix.
    """
    vix_class = classify_vix(vix)

    if not vix_class["sell_premium"]:
        return {
            "action":   "SKIP",
            "reason":   f"VIX too low ({vix:.1f}) — premium not worth the risk",
            "size":     0,
            "strategy": "No trade this week",
        }

    # Check for event risk dates
    next_thursday = date.today() + timedelta(days=(3 - date.today().weekday()) % 7 or 7)
    if next_thursday in NSE_EVENT_DATES:
        return {
            "action":   "SKIP",
            "reason":   f"Event risk: {NSE_EVENT_DATES[next_thursday]} on {next_thursday}",
            "size":     0,
            "strategy": "Skip — event risk week",
        }

    size      = vix_class["size"]
    strategy  = "T3 Iron Condor"   # always IC per user preference

    # Size adjustment for regime
    if regime == "Bear" and vix < 20:
        size *= 0.5
        note = "Half size — bear market + moderate VIX"
    elif regime == "Sideways" and vix > 16:
        size *= 1.0
        note = "Full size — ideal IC conditions"
    elif regime == "Bull" and vix > 16:
        size *= 1.0
        note = "Full size — bull trend, widen call wing"
    elif regime == "Bear" and vix > 20:
        size *= 0.75
        note = "Reduced size — bear + high vol, risk managed"
    else:
        note = "Standard sizing"

    # VIX above 20-day average = extra premium confirmation
    if vix_above_avg:
        note += " · VIX above 20d avg ✓ (premium elevated)"

    return {
        "action":   "PLACE",
        "strategy": strategy,
        "size":     round(size, 2),
        "note":     note,
        "vix_zone": vix_class["zone"],
        "vix_emoji":vix_class["emoji"],
    }

# ─────────────────────────────────────────────────────────────────────────────
# FUTURES SIGNAL — 2 of 3 indicators must align
# ─────────────────────────────────────────────────────────────────────────────
def get_futures_signal(df: pd.DataFrame, vix: float) -> dict:
    """
    Check 2/3 signal alignment for futures kicker.
    Returns direction, score, and which signals fired.
    """
    cur  = df.iloc[-1]
    prev = df.iloc[-2]

    price    = float(cur["close"])
    rsi      = float(cur["rsi"])
    rsi_prev = float(prev["rsi"])
    macd_h   = float(cur["macd_hist"])
    macd_p   = float(prev["macd_hist"])
    bb_upper = float(cur["bb_upper"])
    bb_lower = float(cur["bb_lower"])
    sma50    = float(cur["sma50"])
    sma200   = float(cur["sma200"])

    # ── Bullish signals ───────────────────────────────────────────────────────
    b_rsi  = rsi > 55 and rsi > rsi_prev and rsi_prev < 55   # RSI crossing above 55
    b_macd = macd_h > 0 and macd_p <= 0                       # MACD hist turning positive
    b_bb   = price > bb_upper                                  # BB breakout upward

    # ── Bearish signals ───────────────────────────────────────────────────────
    s_rsi  = rsi < 45 and rsi < rsi_prev and rsi_prev > 45    # RSI crossing below 45
    s_macd = macd_h < 0 and macd_p >= 0                        # MACD hist turning negative
    s_bb   = price < bb_lower                                   # BB breakdown downward

    bull_score = sum([b_rsi, b_macd, b_bb])
    bear_score = sum([s_rsi, s_macd, s_bb])

    # Don't enter futures if VIX > 25 (too dangerous for directional)
    if vix > 25:
        return {
            "direction":   "NONE",
            "reason":      f"VIX {vix:.1f} > 25 — too volatile for futures kicker",
            "bull_score":  bull_score,
            "bear_score":  bear_score,
            "signals":     {},
        }

    if bull_score >= 2 and bull_score > bear_score:
        fired = []
        if b_rsi:  fired.append("RSI crossed above 55")
        if b_macd: fired.append("MACD hist turned positive")
        if b_bb:   fired.append(f"Price broke above BB upper ({bb_upper:.0f})")
        sl_level = round(price * (1 - FUT_SL_PCT), 0)
        target   = round(price * 1.02, 0)   # 2% target display
        return {
            "direction":  "BUY",
            "score":      bull_score,
            "signals_fired": fired,
            "entry_approx":  price,
            "stop_loss":     sl_level,
            "sl_points":     round(price - sl_level, 0),
            "target":        target,
            "bull_score":    bull_score,
            "bear_score":    bear_score,
        }

    elif bear_score >= 2 and bear_score > bull_score:
        fired = []
        if s_rsi:  fired.append("RSI crossed below 45")
        if s_macd: fired.append("MACD hist turned negative")
        if s_bb:   fired.append(f"Price broke below BB lower ({bb_lower:.0f})")
        sl_level = round(price * (1 + FUT_SL_PCT), 0)
        target   = round(price * 0.98, 0)
        return {
            "direction":  "SELL",
            "score":      bear_score,
            "signals_fired": fired,
            "entry_approx":  price,
            "stop_loss":     sl_level,
            "sl_points":     round(sl_level - price, 0),
            "target":        target,
            "bull_score":    bull_score,
            "bear_score":    bear_score,
        }

    else:
        # Near signal check — 1/3 aligned
        near_bull = bull_score == 1
        near_bear = bear_score == 1
        missing_bull, missing_bear = [], []
        if not b_rsi:  missing_bull.append(f"RSI needs >55 (now {rsi:.1f})")
        if not b_macd: missing_bull.append(f"MACD hist needs >0 (now {macd_h:+.1f})")
        if not b_bb:   missing_bull.append(f"Price needs >{bb_upper:.0f} (now {price:.0f})")
        if not s_rsi:  missing_bear.append(f"RSI needs <45 (now {rsi:.1f})")
        if not s_macd: missing_bear.append(f"MACD hist needs <0 (now {macd_h:+.1f})")
        if not s_bb:   missing_bear.append(f"Price needs <{bb_lower:.0f} (now {price:.0f})")

        return {
            "direction":    "NONE",
            "reason":       "Less than 2/3 signals aligned",
            "bull_score":   bull_score,
            "bear_score":   bear_score,
            "missing_bull": missing_bull,
            "missing_bear": missing_bear,
        }

def check_futures_roll(df: pd.DataFrame, state: dict) -> dict:
    """
    Wednesday check — should we roll or close existing futures position?
    """
    pos = state.get("futures_position")
    if not pos:
        return {"action": "NONE", "reason": "No open futures position"}

    cur        = df.iloc[-1]
    price      = float(cur["close"])
    rsi        = float(cur["rsi"])
    macd_h     = float(cur["macd_hist"])
    bb_upper   = float(cur["bb_upper"])
    bb_lower   = float(cur["bb_lower"])
    direction  = pos["direction"]
    entry      = pos["entry_price"]
    sl         = pos["stop_loss"]

    # Check if SL already hit
    if direction == "BUY" and price <= sl:
        pnl = round((price - entry) * FUT_LOT_SIZE, 0)
        return {"action": "CLOSE", "reason": "Stop loss hit",
                "pnl_approx": pnl, "direction": direction}

    if direction == "SELL" and price >= sl:
        pnl = round((entry - price) * FUT_LOT_SIZE, 0)
        return {"action": "CLOSE", "reason": "Stop loss hit",
                "pnl_approx": pnl, "direction": direction}

    # Re-check if signal still active
    b_rsi  = rsi > 55
    b_macd = macd_h > 0
    b_bb   = price > bb_upper
    s_rsi  = rsi < 45
    s_macd = macd_h < 0
    s_bb   = price < bb_lower

    if direction == "BUY":
        score = sum([b_rsi, b_macd, b_bb])
        pnl   = round((price - entry) * FUT_LOT_SIZE, 0)
        if score >= 2:
            return {"action": "ROLL", "reason": f"Signal still active ({score}/3)",
                    "pnl_approx": pnl, "direction": direction,
                    "new_sl": round(price * (1 - FUT_SL_PCT), 0)}
        else:
            return {"action": "CLOSE", "reason": f"Signal weakened ({score}/3)",
                    "pnl_approx": pnl, "direction": direction}

    elif direction == "SELL":
        score = sum([s_rsi, s_macd, s_bb])
        pnl   = round((entry - price) * FUT_LOT_SIZE, 0)
        if score >= 2:
            return {"action": "ROLL", "reason": f"Signal still active ({score}/3)",
                    "pnl_approx": pnl, "direction": direction,
                    "new_sl": round(price * (1 + FUT_SL_PCT), 0)}
        else:
            return {"action": "CLOSE", "reason": f"Signal weakened ({score}/3)",
                    "pnl_approx": pnl, "direction": direction}

    return {"action": "NONE"}

# ─────────────────────────────────────────────────────────────────────────────
# EQUITY STRATEGY SIGNALS (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────
def golden_cross_signal(df: pd.DataFrame) -> dict:
    cur, prev = df.iloc[-1], df.iloc[-2]
    price  = float(cur["close"])
    sma50  = float(cur["sma50"])
    sma200 = float(cur["sma200"])
    rsi    = float(cur["rsi"])
    regime = get_regime(cur)

    cross_above = float(prev["sma50"]) <= float(prev["sma200"]) and sma50 > sma200
    cross_below = float(prev["sma50"]) >= float(prev["sma200"]) and sma50 < sma200
    gap_pct     = (sma50 - sma200) / sma200 * 100

    if cross_above:
        action, urgency = "🟢 BUY SIGNAL — Golden Cross confirmed", "🚨 ACTION"
    elif cross_below:
        action, urgency = "🔴 SELL SIGNAL — Death Cross confirmed", "🚨 ACTION"
    elif sma50 > sma200:
        action, urgency = "✅ HOLD — Uptrend intact", "📊 MONITOR"
    else:
        action, urgency = f"⏳ WAIT — SMA50 is {abs(gap_pct):.1f}% below SMA200", "👀 WATCHING"

    return {"price": price, "sma50": sma50, "sma200": sma200,
            "gap_pct": gap_pct, "rsi": rsi, "regime": regime,
            "cross_above": cross_above, "cross_below": cross_below,
            "action": action, "urgency": urgency}

def bb_squeeze_signal(name: str, df: pd.DataFrame, regime_filter: bool) -> dict:
    cur, prev = df.iloc[-1], df.iloc[-2]
    price    = float(cur["close"])
    bb_upper = float(cur["bb_upper"])
    bb_mid   = float(cur["bb_mid"])
    bb_lower = float(cur["bb_lower"])
    bb_width = float(cur["bb_width"])
    macd_h   = float(cur["macd_hist"])
    rsi      = float(cur["rsi"])
    sma50    = float(cur["sma50"])
    sma200   = float(cur["sma200"])
    regime   = get_regime(cur)

    cond_bb     = price > bb_upper
    cond_macd   = macd_h > 0
    cond_rsi    = rsi > 55
    cond_regime = (sma50 > sma200) if regime_filter else True
    entry_sig   = cond_bb and cond_macd and cond_rsi and cond_regime
    exit_sig    = price < bb_mid
    near_sig    = sum([cond_bb, cond_macd, cond_rsi]) == 2 and not entry_sig
    pct_to_upper = (bb_upper - price) / price * 100

    if entry_sig:
        action, urgency = "🟢 ENTRY SIGNAL — All conditions met", "🚨 ACTION"
    elif exit_sig:
        action, urgency = "🔴 EXIT SIGNAL — Price below BB midband", "🚨 ACTION"
    elif regime_filter and regime == "Bear":
        action, urgency = "🚫 BLOCKED — Bear regime filter active", "🛡 PROTECTED"
    elif near_sig:
        action, urgency = f"⚡ NEAR SIGNAL — 2/3 conditions met", "👀 WATCH"
    else:
        action, urgency = "⏳ WAIT — Conditions not met", "📊 MONITOR"

    return {"price": price, "bb_upper": bb_upper, "bb_mid": bb_mid,
            "bb_lower": bb_lower, "bb_width": bb_width, "pct_to_upper": pct_to_upper,
            "macd_hist": macd_h, "rsi": rsi, "sma50": sma50, "sma200": sma200,
            "regime": regime, "regime_filter": regime_filter,
            "cond_bb": cond_bb, "cond_macd": cond_macd,
            "cond_rsi": cond_rsi, "cond_regime": cond_regime,
            "entry_signal": entry_sig, "exit_signal": exit_sig,
            "near_signal": near_sig, "action": action, "urgency": urgency}

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE FORMATTERS
# ─────────────────────────────────────────────────────────────────────────────
def ci(b): return "✅" if b else "❌"
def re(r): return {"Bull":"🐂","Bear":"🐻","Sideways":"↔"}.get(r,"")

def fmt_fo_section(nifty_df: pd.DataFrame, vix: float,
                   vix_avg: float, state: dict) -> str:
    cur    = nifty_df.iloc[-1]
    spot   = float(cur["close"])
    regime = get_regime(cur)
    today  = date.today()
    weekday = today.weekday()  # 0=Mon, 1=Tue ... 4=Fri

    ic_rec   = get_ic_strategy(vix, regime, vix > vix_avg)
    strikes  = calc_ic_strikes(spot, vix, regime)
    fut_sig  = get_futures_signal(nifty_df, vix)
    roll_rec = check_futures_roll(nifty_df, state) if weekday == 2 else None  # Wednesday only

    pos = state.get("futures_position")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "📐 *F&O STRATEGY — Nifty*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Spot:    `₹{spot:,.0f}`",
        f"VIX:     `{vix:.1f}` {classify_vix(vix)['emoji']} {classify_vix(vix)['zone']}",
        f"VIX avg: `{vix_avg:.1f}` {'(above avg 📈)' if vix > vix_avg else '(below avg 📉)'}",
        f"Regime:  {re(regime)} `{regime}`",
        "",
    ]

    # ── Iron Condor section ───────────────────────────────────────────────────
    lines.append("*🏦 IRON CONDOR (weekly expiry)*")
    if ic_rec["action"] == "SKIP":
        lines.append(f"⛔ SKIP THIS WEEK")
        lines.append(f"`{ic_rec['reason']}`")
    else:
        lines += [
            f"Action:  `PLACE Iron Condor`",
            f"Size:    `{ic_rec['size']} lot` — {ic_rec.get('note','')}",
            f"",
            f"*Strikes to use (verify at market):*",
            f"  SELL CE: `{strikes['sell_ce']}`  BUY CE: `{strikes['buy_ce']}`",
            f"  SELL PE: `{strikes['sell_pe']}`  BUY PE: `{strikes['buy_pe']}`",
            f"",
            f"  Net premium est: `~₹{strikes['net_premium']}/unit` → `~₹{strikes['net_per_lot']:,}/lot`",
            f"  Max loss if breached: `₹{strikes['max_loss_per_lot']:,}/lot`",
            f"  Stop loss: `If combined short premium doubles — buy back`",
            f"  Exit: `Thursday 2:30 PM or at 50% profit`",
        ]

    lines.append("")

    # ── Futures kicker section ────────────────────────────────────────────────
    lines.append("*🚀 FUTURES KICKER (2/3 signals)*")

    # Wednesday roll check takes priority
    if weekday == 2 and roll_rec and roll_rec["action"] != "NONE":
        if roll_rec["action"] == "ROLL":
            lines += [
                f"🔄 ROLL FUTURES — Signal still active",
                f"Direction: `{roll_rec['direction']}`",
                f"Est P&L on current leg: `₹{roll_rec['pnl_approx']:+,.0f}`",
                f"Action: Close current month → Open next month same direction",
                f"New stop loss: `₹{roll_rec.get('new_sl',0):,.0f}`",
                f"`Reason: {roll_rec['reason']}`",
            ]
        elif roll_rec["action"] == "CLOSE":
            lines += [
                f"🔴 CLOSE FUTURES — Signal weakened",
                f"Direction: `{roll_rec['direction']}`",
                f"Est P&L: `₹{roll_rec['pnl_approx']:+,.0f}`",
                f"Action: Close position today before end of day",
                f"`Reason: {roll_rec['reason']}`",
            ]
    elif pos:
        # Show current position status
        entry = pos.get("entry_price", 0)
        sl    = pos.get("stop_loss", 0)
        direction = pos.get("direction", "")
        cur_pnl = ((spot - entry) if direction == "BUY" else (entry - spot)) * FUT_LOT_SIZE
        lines += [
            f"📌 POSITION OPEN: `{direction}` futures",
            f"Entry: `₹{entry:,.0f}` → Current: `₹{spot:,.0f}`",
            f"Stop loss: `₹{sl:,.0f}` ({abs(spot-sl)/spot*100:.1f}% away)",
            f"Est P&L: `₹{cur_pnl:+,.0f}`",
            f"Next action: Wednesday — check roll signal",
        ]
    elif fut_sig["direction"] in ("BUY", "SELL"):
        d    = fut_sig["direction"]
        sl   = fut_sig["stop_loss"]
        tgt  = fut_sig["target"]
        pnl_at_sl  = round(abs(spot - sl) * FUT_LOT_SIZE, 0)
        pnl_at_tgt = round(abs(tgt - spot) * FUT_LOT_SIZE, 0)
        lines += [
            f"{'🟢 BUY' if d=='BUY' else '🔴 SELL'} FUTURES SIGNAL — {fut_sig['score']}/3 aligned",
            f"Direction:  `{d} Nifty futures`",
            f"Entry:      `~₹{spot:,.0f}` (market price)",
            f"Stop loss:  `₹{sl:,.0f}` (−{fut_sig['sl_points']:.0f} pts, risk ₹{pnl_at_sl:,.0f}/lot)",
            f"Target 2%:  `₹{tgt:,.0f}` (reward ₹{pnl_at_tgt:,.0f}/lot)",
            f"R:R ratio:  `1:{round(pnl_at_tgt/pnl_at_sl,1)}`",
            f"",
            f"*Signals fired:*",
        ]
        for s in fut_sig.get("signals_fired", []):
            lines.append(f"  ✅ {s}")
        lines += [
            f"",
            f"*GTT order:* Place at `₹{sl:,.0f}` immediately after entry",
            f"*Wednesday:* Check roll signal — hold if 2/3 still active",
        ]
    else:
        bull_s = fut_sig.get("bull_score", 0)
        bear_s = fut_sig.get("bear_score", 0)
        lines += [
            f"⏳ NO FUTURES SIGNAL",
            f"Bull signals: `{bull_s}/3`  Bear signals: `{bear_s}/3`",
            f"Need 2/3 aligned. IC only this week.",
        ]
        # Show what's missing for nearest direction
        best     = "bull" if bull_s >= bear_s else "bear"
        missing  = fut_sig.get(f"missing_{best}", [])
        if missing:
            lines.append(f"*To get {best} signal, need:*")
            for m in missing[:2]:
                lines.append(f"  • {m}")

    return "\n".join(lines)

def fmt_golden_cross(a: dict) -> str:
    gd = "above" if a["gap_pct"] > 0 else "below"
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 *NIFTY 50 — Golden Cross*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Price:   `₹{a['price']:,.0f}`\n"
        f"SMA 50:  `₹{a['sma50']:,.0f}`\n"
        f"SMA 200: `₹{a['sma200']:,.0f}`\n"
        f"Gap:     `{a['gap_pct']:+.2f}%` ({gd} SMA200)\n"
        f"RSI:     `{a['rsi']:.1f}`\n"
        f"Regime:  {re(a['regime'])} `{a['regime']}`\n"
        f"\n{a['urgency']}\n"
        f"*{a['action']}*\n"
    )

def fmt_bb(a: dict, name: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *{name} — BB Squeeze*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Price:    `₹{a['price']:,.1f}`\n"
        f"BB Upper: `₹{a['bb_upper']:,.1f}` "
        f"{'🔴 BROKEN' if a['cond_bb'] else '(' + str(round(a['pct_to_upper'],1)) + '% away)'}\n"
        f"BB Width: `{a['bb_width']:.1f}%` "
        f"{'_(squeezed)_' if a['bb_width'] < 4 else '_(normal)_'}\n"
        f"MACD:     `{a['macd_hist']:+.2f}` {ci(a['cond_macd'])}\n"
        f"RSI:      `{a['rsi']:.1f}` {ci(a['cond_rsi'])}\n"
        f"Regime:   {re(a['regime'])} `{a['regime']}`\n"
        f"\n{a['urgency']}\n"
        f"*{a['action']}*\n"
    )

# ─────────────────────────────────────────────────────────────────────────────
# GFS NIFTY 500 SCREENER
# ─────────────────────────────────────────────────────────────────────────────
def calc_rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def get_nifty500_tickers() -> dict:
    """
    Load Nifty 500 list from local cache (populated by gfs_nifty500.py).
    Falls back to a compact 80-stock list if cache unavailable.
    """
    Path(__file__).parent.joinpath("data_cache").mkdir(exist_ok=True)

    if GFS_SCREENER_CACHE.exists():
        age = (datetime.now() - datetime.fromtimestamp(
               GFS_SCREENER_CACHE.stat().st_mtime)).days
        if age < 7:
            try:
                df = pd.read_csv(GFS_SCREENER_CACHE)
                result = {}
                for _, row in df.iterrows():
                    sym  = str(row.get("Symbol","")).strip().upper()
                    name = str(row.get("Company Name","")).strip()[:18]
                    if sym:
                        result[name] = f"{sym}.NS"
                if len(result) >= 50:
                    return result
            except Exception:
                pass

    # Compact fallback — top 80 liquid stocks
    return {
        "Reliance":"RELIANCE.NS","TCS":"TCS.NS","HDFC Bank":"HDFCBANK.NS",
        "Infosys":"INFY.NS","ICICI Bank":"ICICIBANK.NS","HUL":"HINDUNILVR.NS",
        "ITC":"ITC.NS","SBI":"SBIN.NS","Bharti Airtel":"BHARTIARTL.NS",
        "Kotak Bank":"KOTAKBANK.NS","L&T":"LT.NS","Axis Bank":"AXISBANK.NS",
        "Asian Paints":"ASIANPAINT.NS","Maruti":"MARUTI.NS",
        "Bajaj Finance":"BAJFINANCE.NS","HCL Tech":"HCLTECH.NS",
        "Wipro":"WIPRO.NS","UltraCemco":"ULTRACEMCO.NS","ONGC":"ONGC.NS",
        "Tata Motors":"TATAMOTORS.NS","Power Grid":"POWERGRID.NS",
        "NTPC":"NTPC.NS","M&M":"M&M.NS","Titan":"TITAN.NS",
        "Sun Pharma":"SUNPHARMA.NS","Tech Mahindra":"TECHM.NS",
        "Adani Ent.":"ADANIENT.NS","JSW Steel":"JSWSTEEL.NS",
        "Tata Steel":"TATASTEEL.NS","Coal India":"COALINDIA.NS",
        "Bajaj Finserv":"BAJAJFINSV.NS","Dr. Reddy's":"DRREDDY.NS",
        "Cipla":"CIPLA.NS","Eicher Motors":"EICHERMOT.NS",
        "Hero MotoCorp":"HEROMOTOCO.NS","BPCL":"BPCL.NS",
        "Tata Consumer":"TATACONSUM.NS","Britannia":"BRITANNIA.NS",
        "Grasim":"GRASIM.NS","Hindalco":"HINDALCO.NS",
        "IndusInd Bank":"INDUSINDBK.NS","Bajaj Auto":"BAJAJ-AUTO.NS",
        "Adani Ports":"ADANIPORTS.NS","Shree Cement":"SHREECEM.NS",
        "SAIL":"SAIL.NS","Vedanta":"VEDL.NS","Jindal Steel":"JINDALSTEL.NS",
        "Torrent Pharma":"TORNTPHARM.NS","Force Motors":"FORCEMOT.NS",
        "BEL":"BEL.NS","MCX":"MCX.NS","NTPC Ltd":"NTPC.NS",
        "Trent":"TRENT.NS","DLF":"DLF.NS","Godrej Props":"GODREJPROP.NS",
        "HAL":"HAL.NS","Havells":"HAVELLS.NS","Pidilite":"PIDILITIND.NS",
        "Info Edge":"NAUKRI.NS","Ambuja Cem":"AMBUJACEM.NS",
        "Lupin":"LUPIN.NS","Zomato":"ZOMATO.NS","TVS Motor":"TVSMOTOR.NS",
        "Varun Bev":"VBL.NS","Cholamandalam":"CHOLAFIN.NS",
        "Muthoot Fin":"MUTHOOTFIN.NS","AU Small Fin":"AUBANK.NS",
        "Federal Bank":"FEDERALBNK.NS","IDFC First":"IDFCFIRSTB.NS",
        "Polycab":"POLYCAB.NS","Persistent":"PERSISTENT.NS",
        "LTIMindtree":"LTIM.NS","Mphasis":"MPHASIS.NS",
        "Page Inds":"PAGEIND.NS","Astral":"ASTRAL.NS",
        "Torrent Power":"TORNTPOWER.NS","CG Power":"CGPOWER.NS",
        "ABB India":"ABB.NS","Siemens":"SIEMENS.NS",
        "GE Vernova":"GET&D.NS","Bharat Forge":"BHARATFORG.NS",
    }

def fetch_mtf_rsi(ticker: str) -> dict | None:
    """
    Fetch daily OHLCV, compute daily/weekly/monthly RSI.
    Returns dict with all RSI values or None on failure.
    """
    try:
        raw = yf.download(ticker, period="500d", progress=False, auto_adjust=True)
        if raw is None or len(raw) < 100:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        close  = raw["Close"].squeeze()
        open_  = raw["Open"].squeeze()
        high   = raw["High"].squeeze()
        low    = raw["Low"].squeeze()

        rsi_d  = calc_rsi_series(close, 14)
        wc     = close.resample("W-FRI").last().dropna()
        rsi_w  = calc_rsi_series(wc, 14).reindex(close.index, method="ffill")
        mc     = close.resample("ME").last().dropna()
        rsi_m  = calc_rsi_series(mc, 14).reindex(close.index, method="ffill")

        # EMA50 and SMA200 for quality check
        ema50  = close.ewm(span=50, adjust=False).mean()
        sma200 = close.rolling(200).mean()

        # Latest values
        rsi_d_val  = float(rsi_d.iloc[-1])
        rsi_w_val  = float(rsi_w.iloc[-1])
        rsi_m_val  = float(rsi_m.iloc[-1])
        price      = float(close.iloc[-1])
        is_green   = float(close.iloc[-1]) > float(open_.iloc[-1])
        is_red     = float(close.iloc[-1]) < float(open_.iloc[-1])
        above_ema50  = price > float(ema50.iloc[-1])
        above_sma200 = price > float(sma200.iloc[-1])
        candle_high  = float(high.iloc[-1])
        candle_low   = float(low.iloc[-1])

        return {
            "rsi_m": round(rsi_m_val, 1),
            "rsi_w": round(rsi_w_val, 1),
            "rsi_d": round(rsi_d_val, 1),
            "price": round(price, 2),
            "is_green":    is_green,
            "is_red":      is_red,
            "above_ema50": above_ema50,
            "above_sma200":above_sma200,
            "candle_high": round(candle_high, 2),
            "candle_low":  round(candle_low, 2),
        }
    except Exception:
        return None

def load_gfs_history() -> dict:
    """
    Auto-load the most recent gfs500_summary CSV.
    Returns {instrument_name_lower: {sharpe, cagr, win_rate, trades}} 
    so every live signal is automatically cross-checked against backtest.
    """
    results_dir = Path(__file__).parent / "results"
    # Pick the most recent gfs500 summary file
    files = sorted(results_dir.glob("gfs500_summary_*.csv"), reverse=True)
    if not files:
        # Also check plain gfs summary from 50-stock run
        files = sorted(results_dir.glob("gfs_summary_*.csv"), reverse=True)
    if not files:
        return {}

    try:
        df = pd.read_csv(files[0])
        history = {}
        for _, row in df.iterrows():
            inst  = str(row.get("instrument","")).strip().lower()
            sharpe = float(row.get("sharpe", 0) or 0)
            cagr   = float(row.get("cagr",   0) or 0)
            wr     = float(row.get("win_rate",0) or 0)
            trades = int(row.get("total_trades", 0) or 0)
            # Keep the best Sharpe result per instrument (Basic vs Advanced)
            if inst not in history or sharpe > history[inst]["sharpe"]:
                history[inst] = {
                    "sharpe": round(sharpe, 2),
                    "cagr":   round(cagr, 2),
                    "win_rate": round(wr, 1),
                    "trades": trades,
                    "strategy": str(row.get("strategy","")),
                }
        return history
    except Exception:
        return {}

def match_history(name: str, history: dict) -> dict | None:
    """
    Fuzzy match a stock name against the history dict.
    The backtest uses full company names; screener may use shorter names.
    Returns history entry or None if no match found.
    """
    name_lower = name.lower().strip()
    # Direct match
    if name_lower in history:
        return history[name_lower]
    # Partial match — backtest name contains screener name or vice versa
    for hist_name, data in history.items():
        if (name_lower[:8] in hist_name or
            hist_name[:8] in name_lower):
            return data
    return None

def run_gfs_screener(instruments: dict) -> tuple[list, list]:
    """
    Scan all instruments for GFS long and inverse GFS short signals.
    Auto-loads historical backtest results and tags each signal.
    Returns (long_signals, short_signals) — each a list of dicts.
    """
    # Auto-load history — no manual list needed
    history = load_gfs_history()

    long_signals  = []
    short_signals = []

    for name, ticker in instruments.items():
        data = fetch_mtf_rsi(ticker)
        if data is None:
            continue

        rsi_m = data["rsi_m"]
        rsi_w = data["rsi_w"]
        rsi_d = data["rsi_d"]

        # Auto-check historical validation
        hist = match_history(name, history)
        historically_validated = (
            hist is not None and
            hist["sharpe"] > 0.5 and
            hist["trades"] >= 5 and
            hist["cagr"] > 0
        )

        # ── GFS Long: G>60, F>60, S 35-45, green candle ──────────────────
        if (rsi_m >= GFS_LONG_M_MIN and
            rsi_w >= GFS_LONG_W_MIN and
            GFS_LONG_D_MIN <= rsi_d < GFS_LONG_D_MAX and
            data["is_green"]):

            quality = rsi_m * 0.4 + rsi_w * 0.3 + (45 - rsi_d) * 0.3
            # Boost quality score for historically validated stocks
            if historically_validated:
                quality += hist["sharpe"] * 5

            long_signals.append({
                "name":         name,
                "ticker":       ticker,
                "rsi_m":        rsi_m,
                "rsi_w":        rsi_w,
                "rsi_d":        rsi_d,
                "price":        data["price"],
                "entry_above":  data["candle_high"],
                "stop_below":   data["candle_low"],
                "above_ema50":  data["above_ema50"],
                "above_sma200": data["above_sma200"],
                "quality":      round(quality, 1),
                "advanced":     data["above_ema50"] and data["above_sma200"],
                "hist":         hist,
                "validated":    historically_validated,
            })

        # ── Inverse GFS Short: G<40, F<40, S 55-65, red candle ────────────
        elif (rsi_m < GFS_SHORT_M_MAX and
              rsi_w < GFS_SHORT_W_MAX and
              GFS_SHORT_D_MIN <= rsi_d < GFS_SHORT_D_MAX and
              data["is_red"]):

            quality = (40 - rsi_m) * 0.4 + (40 - rsi_w) * 0.3 + (rsi_d - 55) * 0.3

            short_signals.append({
                "name":        name,
                "ticker":      ticker,
                "rsi_m":       rsi_m,
                "rsi_w":       rsi_w,
                "rsi_d":       rsi_d,
                "price":       data["price"],
                "entry_below": data["candle_low"],
                "stop_above":  data["candle_high"],
                "quality":     round(quality, 1),
                "hist":        hist,
                "validated":   historically_validated,
            })

    # Sort: validated signals first, then by quality score
    long_signals.sort(key=lambda x: (-int(x["validated"]), -x["quality"]))
    short_signals.sort(key=lambda x: (-int(x["validated"]), -x["quality"]))
    return long_signals, short_signals

def fmt_gfs_section(long_sigs: list, short_sigs: list) -> str:
    """Compact GFS section — one line per signal, detail only for validated."""
    lines = ["🎯 *GFS Signals (Nifty 500)*"]

    # Long signals
    if long_sigs:
        val   = [s for s in long_sigs if s.get("validated")]
        unval = [s for s in long_sigs if not s.get("validated")]

        # Validated — show entry + stop + backtest tag
        for s in val[:3]:
            h = s.get("hist", {})
            lines.append(
                f"  🟢⭐ *{s['name']}* `₹{s['price']:.0f}` "
                f"| RSI D:`{s['rsi_d']}`\n"
                f"     ➡`₹{s['entry_above']:.0f}` 🛡`₹{s['stop_below']:.0f}` "
                f"| _{h.get('sharpe','?')} Sharpe · {h.get('win_rate','?')}% WR_"
            )

        # Unvalidated — one compact line each
        if unval:
            names = ", ".join(
                f"{s['name'].split()[0]} D:{s['rsi_d']}"
                for s in unval[:4]
            )
            lines.append(f"  🟢⚪ _{names}_ — no backtest history")

        if len(long_sigs) > 3 + len(unval):
            lines.append(f"  _...{len(long_sigs)-3-len(unval)} more_")
    else:
        lines.append("  🟢 No GFS long signals")

    # Short signals
    if short_sigs:
        val = [s for s in short_sigs if s.get("validated")]
        for s in short_sigs[:3]:
            h = s.get("hist", {})
            tag = "⭐" if s.get("validated") else "⚪"
            lines.append(
                f"  🔴{tag} *{s['name']}* `₹{s['price']:.0f}` "
                f"| RSI D:`{s['rsi_d']}`\n"
                f"     ➡ below `₹{s['entry_below']:.0f}` 🛡`₹{s['stop_above']:.0f}`"
            )
        if len(short_sigs) > 3:
            lines.append(f"  _...{len(short_sigs)-3} more short signals_")
    else:
        lines.append("  🔴 No inverse GFS short signals")

    return "\n".join(lines)


def build_message(gc: dict, bb_results: list, long_sigs: list, short_sigs: list,
                  vix: float, vix_avg: float, state: dict,
                  fut_sig: dict, weekday: int, day_str: str) -> str:
    """
    Single compact message — everything fits on one phone screen.
    Structure:
      Header (2 lines)
      Market snapshot (3 lines)
      IC this week (3 lines)
      Futures kicker (2 lines)
      GFS signals (compact)
      BB Squeeze — only if action needed (3 lines each)
      Golden Cross — one line
    """
    today     = date.today()
    day_emoji = {"Monday":"🌅","Tuesday":"☀️","Wednesday":"⚡",
                 "Thursday":"🎯","Friday":"🏁",
                 "Saturday":"🗓","Sunday":"🗓"}.get(day_str, "📅")

    # Collect all actions for header
    actions = []
    if gc["cross_above"]: actions.append("📈 Golden Cross BUY")
    if gc["cross_below"]: actions.append("📉 Death Cross SELL")
    bb_actions = [(n, r) for n, r in bb_results
                  if r["entry_signal"] or r["exit_signal"]]
    for n, r in bb_actions:
        actions.append(f"{'✅' if r['entry_signal'] else '❌'} BB {n}")
    if fut_sig["direction"] in ("BUY","SELL"):
        actions.append(f"🚀 Futures {fut_sig['direction']}")
    validated_long = [s for s in long_sigs if s.get("validated")]
    if validated_long:
        actions.append(f"⭐ GFS: {', '.join(s['name'].split()[0] for s in validated_long[:2])}")
    if weekday == 2 and state.get("futures_position"):
        actions.append("⚡ Roll futures check")

    # ── HEADER ────────────────────────────────────────────────────────────────
    msg = f"🔔 *{day_emoji} {day_str} {today.strftime('%d %b')}*\n"
    if actions:
        msg += "🚨 *" + " · ".join(actions[:3]) + "*\n"
    else:
        msg += "📊 _Monitoring — no action today_\n"
    msg += "\n"

    # ── MARKET SNAPSHOT (3 lines) ─────────────────────────────────────────────
    vix_class = classify_vix(vix)
    ic_rec    = get_ic_strategy(vix, gc["regime"], vix > vix_avg)
    strikes   = calc_ic_strikes(float(gc["price"]), vix, gc["regime"])

    msg += (
        f"*Market* {re(gc['regime'])} `₹{gc['price']:,.0f}` "
        f"| VIX `{vix:.1f}` {vix_class['emoji']} "
        f"| RSI `{gc['rsi']:.0f}`\n"
        f"SMA50 `{gc['sma50']:,.0f}` vs SMA200 `{gc['sma200']:,.0f}` "
        f"({gc['gap_pct']:+.1f}%)\n"
    )

    # Wednesday futures roll
    pos = state.get("futures_position")
    if weekday == 2 and pos:
        nifty_df_temp = fetch(NIFTY_TICKER)
        if nifty_df_temp is not None:
            nifty_df_temp = add_indicators(nifty_df_temp)
            roll = check_futures_roll(nifty_df_temp, state)
            if roll["action"] != "NONE":
                msg += (
                    f"⚡ *Futures Roll:* `{roll['action']}` — {roll['reason']} "
                    f"| P&L `₹{roll.get('pnl_approx',0):+,.0f}`\n"
                )

    msg += "\n"

    # ── IRON CONDOR (3 lines) ─────────────────────────────────────────────────
    if ic_rec["action"] == "PLACE":
        msg += (
            f"*🏦 IC this week* `{ic_rec.get('size',1)} lot` "
            f"— {ic_rec.get('note','').split('·')[0].strip()}\n"
            f"  CE: SELL`{strikes['sell_ce']}` BUY`{strikes['buy_ce']}` "
            f"| PE: SELL`{strikes['sell_pe']}` BUY`{strikes['buy_pe']}`\n"
            f"  Premium ~`₹{strikes['net_per_lot']:,}` "
            f"| MaxLoss `₹{strikes['max_loss_per_lot']:,}` "
            f"| Exit Thu 2:30PM\n"
        )
    else:
        msg += f"*🏦 IC this week:* ⛔ SKIP — {ic_rec.get('reason','')}\n"

    msg += "\n"

    # ── FUTURES KICKER (1-2 lines) ────────────────────────────────────────────
    if fut_sig["direction"] in ("BUY","SELL"):
        sl   = fut_sig["stop_loss"]
        tgt  = fut_sig["target"]
        spot = gc["price"]
        msg += (
            f"*🚀 Futures:* `{fut_sig['direction']}` "
            f"Entry ~`₹{spot:,.0f}` "
            f"SL`₹{sl:,.0f}` Tgt`₹{tgt:,.0f}` "
            f"({fut_sig['score']}/3 signals)\n"
            f"  {' · '.join(fut_sig.get('signals_fired',[]))}\n"
        )
    elif pos:
        entry = pos.get("entry_price", 0)
        sl_p  = pos.get("stop_loss", 0)
        spot  = gc["price"]
        pnl   = (spot - entry) * FUT_LOT_SIZE if pos.get("direction") == "BUY" \
                else (entry - spot) * FUT_LOT_SIZE
        msg += (
            f"*🚀 Futures:* Open `{pos.get('direction')}` "
            f"@ `₹{entry:,.0f}` | P&L `₹{pnl:+,.0f}` "
            f"| SL `₹{sl_p:,.0f}`\n"
        )
    else:
        b = fut_sig.get("bull_score", 0)
        s_ = fut_sig.get("bear_score", 0)
        msg += f"*🚀 Futures:* No signal (Bull `{b}/3` Bear `{s_}/3`)\n"

    msg += "\n"

    # ── GFS SIGNALS (compact) ─────────────────────────────────────────────────
    msg += fmt_gfs_section(long_sigs, short_sigs) + "\n\n"

    # ── BB SQUEEZE — only show if action needed ───────────────────────────────
    if bb_actions:
        msg += "*📊 BB Squeeze — Action needed:*\n"
        for name, res in bb_actions:
            action_short = "✅ ENTRY" if res["entry_signal"] else "❌ EXIT"
            msg += (
                f"  {action_short} *{name}* `₹{res['price']:,.0f}` "
                f"RSI:`{res['rsi']:.0f}` "
                f"{re(res['regime'])} {res['regime']}\n"
            )
    else:
        # Collapsed — just one line
        bb_status = " · ".join(
            f"{n}:{r['action'].split()[0]}"
            for n, r in bb_results
        )
        msg += f"*📊 BB:* _{bb_status}_\n"

    msg += "\n"

    # ── GOLDEN CROSS — one line ───────────────────────────────────────────────
    gc_short = gc["action"].split("—")[-1].strip() if "—" in gc["action"] else gc["action"]
    if gc["cross_above"] or gc["cross_below"]:
        msg += f"*📈 Golden Cross:* 🚨 {gc['action']}\n"
    else:
        msg += f"*📈 Nifty GC:* _{gc_short}_\n"

    msg += f"\n_Next: tomorrow 9:30 AM IST_"

    return msg

    validated_long  = [s for s in long_sigs  if s.get("validated")]
    unvalidated_long = [s for s in long_sigs if not s.get("validated")]
    validated_short = [s for s in short_sigs if s.get("validated")]
    unvalidated_short = [s for s in short_sigs if not s.get("validated")]

    def fmt_long_signal(s: dict) -> str:
        h = s.get("hist")
        validated = s.get("validated", False)

        # Validation badge
        if validated and h:
            val_line = (f"  ⭐ _Backtest: Sharpe {h['sharpe']} · "
                        f"CAGR {h['cagr']}% · WR {h['win_rate']}% "
                        f"({h['trades']} trades)_")
        else:
            val_line = "  ⚪ _No backtest validation — trade with caution_"

        adv_tag = " 🔵" if s["advanced"] else ""
        return (
            f"*{s['name']}*{adv_tag} `₹{s['price']:.0f}`\n"
            f"  RSI: M`{s['rsi_m']}` W`{s['rsi_w']}` D`{s['rsi_d']}`\n"
            f"  ➡ Entry above: `₹{s['entry_above']:.1f}`\n"
            f"  🛡 Stop below:  `₹{s['stop_below']:.1f}`\n"
            f"  {'✅ Above EMA50+SMA200' if s['advanced'] else '⚠ Check EMA50/SMA200'}\n"
            f"{val_line}"
        )

    def fmt_short_signal(s: dict) -> str:
        h = s.get("hist")
        validated = s.get("validated", False)
        if validated and h:
            val_line = (f"  ⭐ _Backtest: Sharpe {h['sharpe']} · "
                        f"CAGR {h['cagr']}% ({h['trades']} trades)_")
        else:
            val_line = "  ⚪ _No backtest validation_"
        return (
            f"*{s['name']}* `₹{s['price']:.0f}`\n"
            f"  RSI: M`{s['rsi_m']}` W`{s['rsi_w']}` D`{s['rsi_d']}`\n"
            f"  ➡ Entry below: `₹{s['entry_below']:.1f}`\n"
            f"  🛡 Stop above:  `₹{s['stop_above']:.1f}`\n"
            f"{val_line}"
        )

    # ── Long signals ──────────────────────────────────────────────────────────
    if long_sigs:
        lines.append(f"\n🟢 *GFS LONG — {len(long_sigs)} signals "
                     f"({len(validated_long)} ⭐ validated):*")
        lines.append("_Monthly>60 · Weekly>60 · Daily 35-45 · Green candle_")
        lines.append("_Action: BUY above candle high / BUY CE options_\n")

        # Show validated first, then unvalidated (already sorted by quality)
        shown = 0
        for s in long_sigs[:GFS_MAX_DISPLAY]:
            lines.append(fmt_long_signal(s))
            shown += 1

        if len(long_sigs) > GFS_MAX_DISPLAY:
            remaining_validated = sum(
                1 for s in long_sigs[GFS_MAX_DISPLAY:]
                if s.get("validated")
            )
            lines.append(
                f"  _...{len(long_sigs)-GFS_MAX_DISPLAY} more "
                f"({remaining_validated} validated) in screener CSV_"
            )
    else:
        lines.append("\n🟢 *GFS Long:* No active signals today")

    lines.append("")

    # ── Short / Inverse signals ───────────────────────────────────────────────
    if short_sigs:
        lines.append(f"🔴 *INVERSE GFS SHORT — {len(short_sigs)} signals "
                     f"({len(validated_short)} ⭐ validated):*")
        lines.append("_Monthly<40 · Weekly<40 · Daily 55-65 · Red candle_")
        lines.append("_Action: SELL futures below candle low / BUY PE options_\n")

        for s in short_sigs[:GFS_MAX_DISPLAY]:
            lines.append(fmt_short_signal(s))

        if len(short_sigs) > GFS_MAX_DISPLAY:
            lines.append(f"  _...and {len(short_sigs)-GFS_MAX_DISPLAY} more_")
    else:
        lines.append("🔴 *Inverse GFS Short:* No active signals today")

    return "\n".join(lines) + "\n"

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(msg: str, test_mode: bool = False):
    if test_mode:
        print("\n" + "=" * 60)
        print("TELEGRAM MESSAGE PREVIEW:")
        print("=" * 60)
        print(msg.replace("*","").replace("`","").replace("_",""))
        print("=" * 60)
        return
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    for chunk in chunks:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                      "parse_mode": "Markdown"},
                timeout=15,
            )
            if not resp.ok:
                print(f"Telegram error: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Telegram send failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",  action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if date.today().weekday() >= 5 and not args.force:
        print(f"Weekend — skipping. Use --force to override.")
        return

    today   = date.today()
    weekday = today.weekday()
    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    day_str = day_names[weekday]
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running — {day_str}")

    state = load_state()

    # ── Fetch data ─────────────────────────────────────────────────────────────
    print("  Fetching Nifty + VIX...")
    nifty_df = fetch(NIFTY_TICKER)
    if nifty_df is None:
        send_telegram("⚠ Daily monitor failed: Nifty data unavailable.", args.test)
        return
    nifty_df = add_indicators(nifty_df)

    vix = fetch_vix()
    if vix is None:
        vix = float(nifty_df["close"].pct_change().rolling(20).std().iloc[-1]) * 100 * 16
        print(f"  VIX unavailable — using historical vol proxy: {vix:.1f}")
    else:
        print(f"  VIX: {vix:.1f}")

    # VIX 20-day average from VIX series (approximate from hvol)
    try:
        vix_raw = yf.download(VIX_TICKER, period="60d", progress=False)
        if isinstance(vix_raw.columns, pd.MultiIndex):
            vix_raw.columns = vix_raw.columns.get_level_values(0)
        vix_avg = float(vix_raw["Close"].rolling(20).mean().iloc[-1])
    except Exception:
        vix_avg = vix * 0.95   # fallback

    # ── F&O section ────────────────────────────────────────────────────────────
    fo_section = fmt_fo_section(nifty_df, vix, vix_avg, state)

    # ── Update state if new futures signal ────────────────────────────────────
    fut_sig = get_futures_signal(nifty_df, vix)
    if weekday == 0 and fut_sig["direction"] in ("BUY","SELL"):
        # Monday — new signal, record position
        spot = float(nifty_df.iloc[-1]["close"])
        state["futures_position"] = {
            "direction":   fut_sig["direction"],
            "entry_price": spot,
            "stop_loss":   fut_sig["stop_loss"],
            "entry_date":  today.isoformat(),
        }
    elif weekday == 2:
        roll = check_futures_roll(nifty_df, state)
        if roll["action"] == "CLOSE":
            state["futures_position"] = None
        elif roll["action"] == "ROLL":
            spot = float(nifty_df.iloc[-1]["close"])
            if state["futures_position"]:
                state["futures_position"]["stop_loss"] = roll.get("new_sl", spot)
                state["futures_position"]["rolled"]    = True

    save_state(state)

    # ── Equity signals ─────────────────────────────────────────────────────────
    gc_result = golden_cross_signal(nifty_df)
    print(f"  Nifty: ₹{gc_result['price']:,.0f} | {gc_result['regime']} | {gc_result['action'][:35]}")

    bb_results = []
    for name, ticker in BB_STOCKS.items():
        df_ = fetch(ticker)
        if df_ is None:
            continue
        df_ = add_indicators(df_)
        rf  = name in REGIME_FILTER_STOCKS
        res = bb_squeeze_signal(name, df_, rf)
        bb_results.append((name, res))
        print(f"  {name}: {res['action'][:40]}")

    # ── GFS Nifty 500 screener ─────────────────────────────────────────────────
    print("  Running GFS screener on Nifty 500...")
    gfs_instruments = get_nifty500_tickers()
    long_sigs, short_sigs = run_gfs_screener(gfs_instruments)
    print(f"  GFS Long: {len(long_sigs)} signals | GFS Short: {len(short_sigs)} signals")

    # ── Build compact message ──────────────────────────────────────────────────
    full_msg = build_message(
        gc=gc_result, bb_results=bb_results,
        long_sigs=long_sigs, short_sigs=short_sigs,
        vix=vix, vix_avg=vix_avg, state=state,
        fut_sig=fut_sig, weekday=weekday, day_str=day_str,
    )

    send_telegram(full_msg, args.test)
    print(f"  Done ({len(full_msg)} chars)")

if __name__ == "__main__":
    main()
