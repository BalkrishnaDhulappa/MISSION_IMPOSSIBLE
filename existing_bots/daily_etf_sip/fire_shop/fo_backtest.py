#!/usr/bin/env python3
"""
fo_backtest.py — Nifty Futures & Options Backtester
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backtests 8 strategies across Futures + Options on Nifty 50.
Uses Black-Scholes + real India VIX for synthetic options pricing.
No paid data needed — everything from yfinance.

Strategies:
  Futures:    F1 (Trend/SMA), F2 (MACD momentum)
  Directional:D1 (ATM buy), D2 (OTM momentum)
  Theta:      T1 (Straddle), T2 (Strangle), T3 (Iron Condor)
  Spreads:    S1 (Bull Call), S2 (Bear Put)

Usage:
  python fo_backtest.py                          # all strategies
  python fo_backtest.py --strategy F1            # single strategy
  python fo_backtest.py --from 2015-01-01        # custom start date
  python fo_backtest.py --top 5                  # show top 5 only
  python fo_backtest.py --no-telegram            # skip Telegram
"""

import os, sys, json, warnings, argparse, math
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    print("Tip: pip install rich  for coloured output\n")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

START_DATE    = "2004-01-01"
END_DATE      = datetime.today().strftime("%Y-%m-%d")
INITIAL_CAP   = 500_000        # ₹5,00,000 — need more capital for F&O margin
RISK_FREE     = 0.065          # 6.5% annual risk-free rate (India 10yr approx)
WARMUP        = 210            # bars before backtesting

BASE_DIR      = Path(__file__).parent
RESULTS_DIR   = BASE_DIR / "results"
CACHE_DIR     = BASE_DIR / "data_cache"
RESULTS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# NIFTY LOT SIZE HISTORY
# Lot size changed multiple times — wrong size = wrong P&L
# ─────────────────────────────────────────────────────────────────────────────
LOT_SIZE_HISTORY = [
    (date(2000,  1,  1), date(2025,  6, 26), 25),   # original lot size
    (date(2025,  6, 27), date(2025, 12, 31), 75),   # increased Jun 2025
    (date(2026,  1,  1), date(2099, 12, 31), 65),   # current (Jan 2026)
]

def get_lot_size(dt: date) -> int:
    for start, end, size in LOT_SIZE_HISTORY:
        if start <= dt <= end:
            return size
    return 50

# ─────────────────────────────────────────────────────────────────────────────
# EXPIRY CALENDAR
# Weekly: every Thursday (from Feb 2019), else prev Wednesday if holiday
# Monthly: last Thursday of month (always existed)
# ─────────────────────────────────────────────────────────────────────────────
WEEKLY_EXPIRY_START = date(2019, 2, 11)   # Nifty weekly options started

# Known NSE holidays (partial list — major ones 2004-2026)
# In production use NSE holiday calendar API, but this covers ~95% of cases
NSE_HOLIDAYS = {
    date(2024,  1, 26), date(2024,  3, 25), date(2024,  3, 29),
    date(2024,  4, 14), date(2024,  4, 17), date(2024,  5,  1),
    date(2024,  6, 17), date(2024,  8, 15), date(2024, 10,  2),
    date(2024, 11,  1), date(2024, 11, 15), date(2024, 12, 25),
    date(2023,  1, 26), date(2023,  3, 30), date(2023,  4, 14),
    date(2023,  8, 15), date(2023, 10,  2), date(2023, 10, 24),
    date(2023, 11, 27), date(2023, 12, 25),
    date(2022,  1, 26), date(2022,  3, 18), date(2022,  4, 14),
    date(2022,  4, 15), date(2022,  8, 15), date(2022, 10,  5),
    date(2022, 10, 24), date(2022, 10, 26),
}

def prev_trading_day(dt: date) -> date:
    """Return dt itself if trading day, else go back until we find one."""
    while dt.weekday() >= 5 or dt in NSE_HOLIDAYS:
        dt -= timedelta(days=1)
    return dt

def get_monthly_expiry(year: int, month: int) -> date:
    """Last Thursday of the given month, adjusted for holidays."""
    # Find last Thursday
    last_day = date(year, month, 28)
    while True:
        last_day += timedelta(days=1)
        if last_day.month != month:
            break
    last_day -= timedelta(days=1)
    while last_day.weekday() != 3:   # 3 = Thursday
        last_day -= timedelta(days=1)
    return prev_trading_day(last_day)

def get_next_expiry(from_date: date, weekly: bool = False) -> date:
    """Get the next expiry date from a given date."""
    if weekly and from_date >= WEEKLY_EXPIRY_START:
        # Next Thursday
        days_ahead = (3 - from_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        candidate = from_date + timedelta(days=days_ahead)
        return prev_trading_day(candidate)
    else:
        # Next monthly expiry
        year, month = from_date.year, from_date.month
        exp = get_monthly_expiry(year, month)
        if exp <= from_date:
            month += 1
            if month > 12:
                month = 1
                year += 1
            exp = get_monthly_expiry(year, month)
        return exp

def get_dte(from_date: date, expiry: date) -> float:
    """Days to expiry as fraction of year."""
    dte = max((expiry - from_date).days, 0)
    return dte / 365.0

def round_to_strike(price: float, step: int = 50) -> int:
    """Round to nearest Nifty strike (multiples of 50)."""
    return int(round(price / step) * step)

# ─────────────────────────────────────────────────────────────────────────────
# BLACK-SCHOLES PRICING ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _norm_cdf(x: float) -> float:
    """Standard normal CDF — no scipy needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = "CE") -> float:
    """
    Black-Scholes option price.
    S     = spot price
    K     = strike price
    T     = time to expiry in years
    r     = risk-free rate (annual)
    sigma = implied volatility (annual, e.g. 0.15 for 15%)
    option_type = "CE" or "PE"
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # At/after expiry — intrinsic value only
        if option_type == "CE":
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "CE":
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

    return max(price, 0.0)

def bs_greeks(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str = "CE") -> dict:
    """Return Delta, Gamma, Theta, Vega."""
    if T <= 0 or sigma <= 0:
        return {"delta": 1.0 if (option_type=="CE" and S>K) else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf_d1 = _norm_pdf(d1)

    delta = _norm_cdf(d1) if option_type == "CE" else _norm_cdf(d1) - 1
    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    theta = (-(S * pdf_d1 * sigma) / (2 * math.sqrt(T))
             - r * K * math.exp(-r * T) * (_norm_cdf(d2) if option_type=="CE" else _norm_cdf(-d2))
            ) / 365
    vega  = S * pdf_d1 * math.sqrt(T) / 100   # per 1% change in IV

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}

def get_iv(row: pd.Series, fallback_hvol: float) -> float:
    """Get IV from VIX row or fall back to historical vol."""
    vix = row.get("vix", None)
    if vix and not pd.isna(vix) and vix > 0:
        return float(vix) / 100.0
    return fallback_hvol

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nifty_vix(start: str, end: str) -> pd.DataFrame | None:
    """
    Fetch Nifty spot + India VIX, merge on date, compute indicators.
    """
    cache_file = CACHE_DIR / "nifty_vix_merged.parquet"

    # Use cache if fresh (< 1 day old)
    if cache_file.exists():
        age = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).days
        if age < 1:
            try:
                return pd.read_parquet(cache_file)
            except Exception:
                pass

    print("  Downloading Nifty spot data...")
    nifty = yf.download("^NSEI", start=start, end=end,
                        progress=False, auto_adjust=True)
    if nifty is None or nifty.empty:
        return None

    print("  Downloading India VIX data...")
    vix_raw = yf.download("^INDIAVIX", start=start, end=end,
                          progress=False, auto_adjust=True)

    # Flatten MultiIndex
    for df_ in [nifty, vix_raw]:
        if isinstance(df_.columns, pd.MultiIndex):
            df_.columns = df_.columns.get_level_values(0)

    nifty = nifty[["Open","High","Low","Close","Volume"]].copy()
    nifty.columns = ["open","high","low","close","volume"]
    nifty.index = pd.to_datetime(nifty.index)

    if not vix_raw.empty:
        vix = vix_raw[["Close"]].copy()
        vix.columns = ["vix"]
        vix.index = pd.to_datetime(vix.index)
        df = nifty.join(vix, how="left")
    else:
        df = nifty.copy()
        df["vix"] = np.nan

    df = df.dropna(subset=["close"])
    df = _add_indicators(df)
    df.to_parquet(cache_file)
    return df

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]

    for p in [5,10,20,50,100,200]:
        df[f"sma{p}"] = c.rolling(p).mean()
    for p in [9,12,26]:
        df[f"ema{p}"] = c.ewm(span=p, adjust=False).mean()

    df["macd"]        = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = bb_mid + 2*bb_std
    df["bb_lower"] = bb_mid - 2*bb_std
    df["bb_mid"]   = bb_mid

    up   = df["high"].diff()
    dn   = -df["low"].diff()
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    mdm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr   = pd.concat([df["high"]-df["low"],
                      (df["high"]-c.shift()).abs(),
                      (df["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr  = tr.ewm(span=14, adjust=False).mean()
    df["adx"] = (100 * pd.Series(pdm,index=df.index).ewm(span=14,adjust=False).mean() /
                 atr).ewm(span=14, adjust=False).mean()

    # Historical volatility (20-day annualised) — fallback when VIX unavailable
    log_ret = np.log(c / c.shift(1))
    df["hvol"] = log_ret.rolling(20).std() * math.sqrt(252)

    return df.dropna(subset=["sma200","rsi","macd","hvol"])

# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION COSTS — NSE F&O 2025
# ─────────────────────────────────────────────────────────────────────────────
def futures_costs(turnover: float) -> float:
    """Round-trip cost for 1 futures trade."""
    brokerage = 40.0                          # ₹20 each side (Zerodha flat)
    stt       = 0.0002  * turnover            # 0.02% on sell side turnover
    txn       = 0.000173 * turnover * 2       # 0.00173% both sides
    sebi      = 0.000001 * turnover * 2       # ₹10/crore both sides
    gst       = 0.18 * (brokerage/2 + txn)   # 18% on brokerage + txn
    stamp     = 0.00002 * turnover / 2        # 0.002% buy side only
    return brokerage + stt + txn + sebi + gst + stamp

def options_buy_costs(premium_paid: float, lots: int, lot_size: int) -> float:
    """Cost of buying options (STT only on sell side, not buy)."""
    total_prem = premium_paid * lots * lot_size
    brokerage  = 40.0
    txn        = 0.000503 * total_prem * 2
    sebi       = 0.000001 * total_prem * 2
    gst        = 0.18 * (brokerage/2 + txn)
    stamp      = 0.00003 * total_prem         # 0.003% buy side
    # Note: STT on buying options = 0 (paid only on sell/exercise)
    return brokerage + txn + sebi + gst + stamp

def options_sell_costs(premium_received: float, lots: int, lot_size: int) -> float:
    """Cost of selling/closing options (STT on premium received)."""
    total_prem = premium_received * lots * lot_size
    brokerage  = 40.0
    stt        = 0.001 * total_prem            # 0.1% on premium (sell side)
    txn        = 0.000503 * total_prem * 2
    sebi       = 0.000001 * total_prem * 2
    gst        = 0.18 * (brokerage/2 + txn)
    return brokerage + stt + txn + sebi + gst

# ─────────────────────────────────────────────────────────────────────────────
# MARGIN ESTIMATOR (approximate SPAN)
# ─────────────────────────────────────────────────────────────────────────────
def futures_margin(spot: float, lot_size: int) -> float:
    """Approximate initial margin for 1 futures lot (~10% of notional)."""
    return spot * lot_size * 0.10

def short_options_margin(spot: float, lot_size: int) -> float:
    """Approximate SPAN margin for short options (~12% of notional)."""
    return spot * lot_size * 0.12

# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def get_regime(row: pd.Series) -> str:
    if row["close"] > row["sma200"] and row["sma50"] > row["sma200"]:
        return "bull"
    if row["close"] < row["sma200"] and row["sma50"] < row["sma200"]:
        return "bear"
    return "sideways"

# ─────────────────────────────────────────────────────────────────────────────
# METRICS CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────
def calc_metrics(trades: list, equity: list, strategy_name: str) -> dict | None:
    if len(trades) < 5 or not equity:
        return None

    eq_vals = [e["value"] for e in equity]
    init    = float(INITIAL_CAP)
    final   = float(eq_vals[-1])
    years   = max((equity[-1]["date"] - equity[0]["date"]).days / 365.25, 0.5)

    if final <= 0:
        return None

    cagr      = ((final / init) ** (1 / years) - 1) * 100
    total_ret = (final - init) / init * 100

    # Max drawdown
    peak  = init
    max_dd = 0.0
    for v in eq_vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe
    rets = [(eq_vals[i] - eq_vals[i-1]) / eq_vals[i-1]
            for i in range(1, len(eq_vals))]
    mr   = np.mean(rets)
    sr   = np.std(rets)
    sharpe = (mr / sr * math.sqrt(252)) if sr > 1e-9 else 0.0

    calmar    = cagr / max_dd if max_dd > 0 else 0.0
    wins      = [t for t in trades if t["net_pnl"] > 0]
    losses    = [t for t in trades if t["net_pnl"] <= 0]
    win_rate  = len(wins) / len(trades) * 100
    avg_win   = np.mean([t["pnl_pct"] for t in wins])   if wins   else 0.0
    avg_loss  = np.mean([t["pnl_pct"] for t in losses]) if losses else 0.0
    gross_w   = sum(t["net_pnl"] for t in wins)
    gross_l   = abs(sum(t["net_pnl"] for t in losses))
    pf        = gross_w / gross_l if gross_l > 0 else 99.0
    avg_dur   = np.mean([t.get("duration", 0) for t in trades])
    tot_costs = sum(t.get("costs", 0) for t in trades)
    exp       = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)

    regime_stats = {}
    for reg in ["bull", "bear", "sideways"]:
        rt = [t for t in trades if t.get("regime") == reg]
        rw = [t for t in rt if t["net_pnl"] > 0]
        regime_stats[reg] = {
            "trades":    len(rt),
            "win_rate":  round(len(rw)/len(rt)*100, 1) if rt else 0.0,
            "avg_pnl":   round(float(np.mean([t["pnl_pct"] for t in rt])), 2) if rt else 0.0,
            "total_pnl": round(sum(t["net_pnl"] for t in rt), 0),
        }

    annual = {}
    for t in trades:
        yr = str(t.get("exit_date", ""))[:4]
        if yr:
            annual[yr] = round(annual.get(yr, 0.0) + t["net_pnl"], 0)

    exit_bkdn = {}
    for t in trades:
        r = t.get("reason", "unknown")
        exit_bkdn[r] = exit_bkdn.get(r, 0) + 1

    return {
        "strategy":       strategy_name,
        "cagr":           round(cagr, 2),
        "total_return":   round(total_ret, 2),
        "final_value":    round(final, 0),
        "max_dd":         round(max_dd, 2),
        "sharpe":         round(sharpe, 2),
        "calmar":         round(calmar, 2),
        "win_rate":       round(win_rate, 1),
        "total_trades":   len(trades),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(min(pf, 99.0), 2),
        "avg_duration":   round(avg_dur, 1),
        "total_costs":    round(tot_costs, 0),
        "expectancy":     round(exp, 2),
        "years":          round(years, 1),
        "regime_stats":   regime_stats,
        "annual_returns": annual,
        "exit_breakdown": exit_bkdn,
    }

# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════
# STRATEGY ENGINES
# ══════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

# ── F1: Trend Futures (Golden Cross on futures) ───────────────────────────────
def run_f1_trend_futures(df: pd.DataFrame) -> tuple[list, list]:
    """
    Long futures when SMA50 > SMA200, short when SMA50 < SMA200.
    Rolls over at each monthly expiry.
    Uses 1 lot throughout. Margin tracked.
    """
    trades, equity = [], []
    cash    = float(INITIAL_CAP)
    pos     = 0          # +1 long, -1 short, 0 flat
    entry_p = 0.0
    entry_d = None
    entry_reg = "sideways"
    current_expiry = None

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        prev  = df.iloc[i-1]

        regime = get_regime(row)

        # Signal
        bull_signal = float(row["sma50"]) > float(row["sma200"])
        bear_signal = float(row["sma50"]) < float(row["sma200"])

        # Check expiry rollover
        if current_expiry and cur_d >= current_expiry and pos != 0:
            # Force close at expiry, reopen immediately
            margin   = futures_margin(spot, lot)
            turnover = spot * lot
            cost     = futures_costs(turnover)
            gross    = (spot - entry_p) * lot * pos
            net      = gross - cost
            pnl_pct  = net / (entry_p * lot) * 100

            trades.append({
                "strategy":   "F1 Trend Futures",
                "entry_date": entry_d, "exit_date": cur_d,
                "entry_price":round(entry_p,2), "exit_price": round(spot,2),
                "direction":  "LONG" if pos>0 else "SHORT",
                "lot_size":   lot, "gross_pnl": round(gross,2),
                "costs":      round(cost,2), "net_pnl":   round(net,2),
                "pnl_pct":    round(pnl_pct,2), "reason":    "Expiry Rollover",
                "regime":     entry_reg, "duration":  (cur_d-entry_d).days,
            })
            cash += net
            entry_p = spot
            entry_d = cur_d
            current_expiry = get_next_expiry(cur_d, weekly=False)

        # Exit existing position if signal flipped
        if pos == 1 and not bull_signal:
            turnover = spot * lot
            cost     = futures_costs(turnover)
            gross    = (spot - entry_p) * lot
            net      = gross - cost
            pnl_pct  = net / (entry_p * lot) * 100
            trades.append({
                "strategy": "F1 Trend Futures",
                "entry_date": entry_d, "exit_date": cur_d,
                "entry_price": round(entry_p,2), "exit_price": round(spot,2),
                "direction": "LONG", "lot_size": lot,
                "gross_pnl": round(gross,2), "costs": round(cost,2),
                "net_pnl": round(net,2), "pnl_pct": round(pnl_pct,2),
                "reason": "Signal Flip", "regime": entry_reg,
                "duration": (cur_d-entry_d).days,
            })
            cash += net
            pos, entry_p, entry_d = 0, 0.0, None

        elif pos == -1 and not bear_signal:
            turnover = spot * lot
            cost     = futures_costs(turnover)
            gross    = (entry_p - spot) * lot
            net      = gross - cost
            pnl_pct  = net / (entry_p * lot) * 100
            trades.append({
                "strategy": "F1 Trend Futures",
                "entry_date": entry_d, "exit_date": cur_d,
                "entry_price": round(entry_p,2), "exit_price": round(spot,2),
                "direction": "SHORT", "lot_size": lot,
                "gross_pnl": round(gross,2), "costs": round(cost,2),
                "net_pnl": round(net,2), "pnl_pct": round(pnl_pct,2),
                "reason": "Signal Flip", "regime": entry_reg,
                "duration": (cur_d-entry_d).days,
            })
            cash += net
            pos, entry_p, entry_d = 0, 0.0, None

        # Enter new position
        if pos == 0:
            margin = futures_margin(spot, lot)
            if bull_signal and cash > margin * 1.5:
                pos = 1
                entry_p, entry_d, entry_reg = spot, cur_d, regime
                current_expiry = get_next_expiry(cur_d, weekly=False)
            elif bear_signal and cash > margin * 1.5:
                pos = -1
                entry_p, entry_d, entry_reg = spot, cur_d, regime
                current_expiry = get_next_expiry(cur_d, weekly=False)

        port = cash + ((spot - entry_p) * lot * pos if pos != 0 else 0)
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── F2: MACD Futures ──────────────────────────────────────────────────────────
def run_f2_macd_futures(df: pd.DataFrame) -> tuple[list, list]:
    """
    Long futures on MACD crossover above signal with ADX>25.
    Short on MACD crossover below signal with ADX>25.
    5% stop loss from entry.
    """
    trades, equity = [], []
    cash    = float(INITIAL_CAP)
    pos     = 0
    entry_p = 0.0
    entry_d = None
    entry_reg = "sideways"
    stop_p  = 0.0

    for i in range(WARMUP, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot = float(row["close"])
        lot  = get_lot_size(cur_d)
        regime = get_regime(row)

        macd_cross_up   = float(prev["macd"]) <= float(prev["macd_signal"]) and float(row["macd"]) > float(row["macd_signal"])
        macd_cross_down = float(prev["macd"]) >= float(prev["macd_signal"]) and float(row["macd"]) < float(row["macd_signal"])
        adx_strong      = float(row["adx"]) > 25

        def close_pos(reason, exit_p):
            nonlocal cash, pos, entry_p, entry_d, stop_p
            turnover = exit_p * lot
            cost     = futures_costs(turnover)
            gross    = (exit_p - entry_p) * lot * pos
            net      = gross - cost
            pnl_pct  = net / (entry_p * lot) * 100
            trades.append({
                "strategy": "F2 MACD Futures",
                "entry_date": entry_d, "exit_date": cur_d,
                "entry_price": round(entry_p,2), "exit_price": round(exit_p,2),
                "direction": "LONG" if pos>0 else "SHORT", "lot_size": lot,
                "gross_pnl": round(gross,2), "costs": round(cost,2),
                "net_pnl": round(net,2), "pnl_pct": round(pnl_pct,2),
                "reason": reason, "regime": entry_reg,
                "duration": (cur_d-entry_d).days,
            })
            cash += net
            pos, entry_p, entry_d, stop_p = 0, 0.0, None, 0.0

        if pos == 1:
            if float(row["low"]) <= stop_p:
                close_pos("Stop Loss", stop_p)
            elif macd_cross_down:
                close_pos("Signal Exit", spot)

        elif pos == -1:
            if float(row["high"]) >= stop_p:
                close_pos("Stop Loss", stop_p)
            elif macd_cross_up:
                close_pos("Signal Exit", spot)

        if pos == 0:
            margin = futures_margin(spot, lot)
            if macd_cross_up and adx_strong and cash > margin * 1.5:
                pos = 1
                entry_p, entry_d, entry_reg = spot, cur_d, regime
                stop_p = spot * 0.95
            elif macd_cross_down and adx_strong and cash > margin * 1.5:
                pos = -1
                entry_p, entry_d, entry_reg = spot, cur_d, regime
                stop_p = spot * 1.05

        port = cash + ((spot - entry_p) * lot * pos if pos != 0 else 0)
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── D1: ATM CE/PE Buy (directional) ──────────────────────────────────────────
def run_d1_atm_buy(df: pd.DataFrame, weekly: bool = True) -> tuple[list, list]:
    """
    Buy ATM CE when bullish (SMA50>SMA200 + RSI>55).
    Buy ATM PE when bearish (SMA50<SMA200 + RSI<45).
    Exit: 50% loss OR 100% gain OR 3 days before expiry.
    """
    strat_name = f"D1 ATM {'Weekly' if weekly else 'Monthly'} Buy"
    trades, equity = [], []
    cash    = float(INITIAL_CAP)
    pos     = None  # dict with position details

    for i in range(WARMUP, len(df)):
        row  = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        bull = float(row["sma50"]) > float(row["sma200"]) and float(row["rsi"]) > 55
        bear = float(row["sma50"]) < float(row["sma200"]) and float(row["rsi"]) < 45

        if pos:
            dte  = get_dte(cur_d, pos["expiry"])
            curr_prem = bs_price(spot, pos["strike"], dte, RISK_FREE, iv, pos["otype"])
            cost_base = pos["entry_prem"]

            pnl_pct = (curr_prem - cost_base) / cost_base * 100 if cost_base > 0 else 0

            exit_reason = None
            if dte <= 3/365:
                exit_reason = "Near Expiry"
            elif pnl_pct <= -50:
                exit_reason = "Stop Loss 50%"
            elif pnl_pct >= 100:
                exit_reason = "Target 100%"

            if exit_reason:
                exit_prem = max(curr_prem, 0.05)
                sell_cost = options_sell_costs(exit_prem, 1, lot)
                gross     = (exit_prem - cost_base) * lot
                net       = gross - sell_cost - pos["buy_cost"]
                pnl_p     = net / (cost_base * lot) * 100
                trades.append({
                    "strategy": strat_name, "option_type": pos["otype"],
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "strike": pos["strike"], "expiry": pos["expiry"],
                    "entry_prem": round(cost_base,2), "exit_prem": round(exit_prem,2),
                    "lot_size": lot, "gross_pnl": round(gross,2),
                    "costs": round(sell_cost+pos["buy_cost"],2),
                    "net_pnl": round(net,2), "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                })
                cash += (exit_prem * lot) - sell_cost
                pos = None

        if pos is None:
            if bull or bear:
                otype   = "CE" if bull else "PE"
                strike  = round_to_strike(spot)
                expiry  = get_next_expiry(cur_d, weekly=(weekly and cur_d >= WEEKLY_EXPIRY_START))
                dte     = get_dte(cur_d, expiry)
                prem    = bs_price(spot, strike, dte, RISK_FREE, iv, otype)
                bc      = options_buy_costs(prem, 1, lot)

                if prem > 0.5 and cash > prem * lot + bc + 5000:
                    pos = {
                        "otype": otype, "strike": strike, "expiry": expiry,
                        "entry_prem": prem, "entry_date": cur_d,
                        "buy_cost": bc, "iv_entry": iv, "regime": regime,
                    }
                    cash -= (prem * lot + bc)

        port = cash + ((bs_price(spot, pos["strike"],
                                  get_dte(cur_d, pos["expiry"]),
                                  RISK_FREE, iv, pos["otype"])
                        * lot) if pos else 0)
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── T1: Weekly ATM Straddle (sell) ────────────────────────────────────────────
def run_t1_straddle(df: pd.DataFrame) -> tuple[list, list]:
    """
    Every Monday: sell ATM CE + ATM PE (same strike = ATM straddle).
    Close on Thursday 2PM (day before expiry) or stop loss 2x premium.
    Only when VIX > 12 (enough premium worth selling).
    """
    trades, equity = [], []
    cash    = float(INITIAL_CAP)
    pos     = None
    DAYS_BEFORE_EXPIRY_TO_CLOSE = 0  # close on expiry day itself

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        # Weekly straddle only available from Feb 2019
        if cur_d < WEEKLY_EXPIRY_START:
            if i % 5 == 0:
                equity.append({"date": cur_d, "value": cash, "regime": regime})
            continue

        if pos:
            dte      = get_dte(cur_d, pos["expiry"])
            curr_ce  = bs_price(spot, pos["strike"], dte, RISK_FREE, iv, "CE")
            curr_pe  = bs_price(spot, pos["strike"], dte, RISK_FREE, iv, "PE")
            curr_val = curr_ce + curr_pe
            coll     = pos["premium_collected"]

            pnl_pct  = (coll - curr_val) / coll * 100 if coll > 0 else 0
            exit_reason = None

            if dte <= 0 or cur_d >= pos["expiry"]:
                exit_reason = "Expiry"
            elif (curr_val / coll) >= 2.0:
                exit_reason = "Stop Loss 2x"

            if exit_reason:
                buy_back_cost = options_buy_costs(curr_val, 1, lot)
                gross = (coll - curr_val) * lot
                net   = gross - buy_back_cost - pos["sell_cost"]
                pnl_p = net / (coll * lot) * 100
                trades.append({
                    "strategy": "T1 ATM Straddle",
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "strike": pos["strike"], "expiry": pos["expiry"],
                    "premium_collected": round(coll,2),
                    "premium_at_exit": round(curr_val,2),
                    "lot_size": lot, "gross_pnl": round(gross,2),
                    "costs": round(buy_back_cost+pos["sell_cost"],2),
                    "net_pnl": round(net,2), "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                })
                cash += (coll - curr_val) * lot - buy_back_cost
                pos = None

        # Enter new straddle on Monday
        if pos is None and cur_d.weekday() == 0:
            expiry = get_next_expiry(cur_d, weekly=True)
            dte    = get_dte(cur_d, expiry)
            strike = round_to_strike(spot)
            prem_ce = bs_price(spot, strike, dte, RISK_FREE, iv, "CE")
            prem_pe = bs_price(spot, strike, dte, RISK_FREE, iv, "PE")
            total_prem = prem_ce + prem_pe
            sc = options_sell_costs(total_prem, 1, lot)
            margin = short_options_margin(spot, lot) * 2

            vix_val = row.get("vix", 15)
            if (pd.isna(vix_val) or vix_val == 0):
                vix_val = 15

            if float(vix_val) > 12 and cash > margin + 10000 and total_prem > 50:
                pos = {
                    "strike": strike, "expiry": expiry,
                    "premium_collected": total_prem,
                    "entry_date": cur_d, "sell_cost": sc,
                    "iv_entry": iv, "regime": regime,
                }
                cash += total_prem * lot - sc

        curr_strad_val = 0
        if pos:
            dte = get_dte(cur_d, pos["expiry"])
            curr_strad_val = (bs_price(spot, pos["strike"], dte, RISK_FREE, iv, "CE") +
                              bs_price(spot, pos["strike"], dte, RISK_FREE, iv, "PE")) * lot
        port = cash - curr_strad_val
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── T2: Weekly Strangle (sell OTM) ───────────────────────────────────────────
def run_t2_strangle(df: pd.DataFrame) -> tuple[list, list]:
    """
    Sell 1% OTM CE + 1% OTM PE every Monday.
    Close at expiry or 2x premium stop.
    """
    trades, equity = [], []
    cash = float(INITIAL_CAP)
    pos  = None

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        if cur_d < WEEKLY_EXPIRY_START:
            if i % 5 == 0:
                equity.append({"date": cur_d, "value": cash, "regime": regime})
            continue

        if pos:
            dte     = get_dte(cur_d, pos["expiry"])
            curr_ce = bs_price(spot, pos["strike_ce"], dte, RISK_FREE, iv, "CE")
            curr_pe = bs_price(spot, pos["strike_pe"], dte, RISK_FREE, iv, "PE")
            curr_val= curr_ce + curr_pe
            coll    = pos["premium_collected"]

            exit_reason = None
            if dte <= 0 or cur_d >= pos["expiry"]:
                exit_reason = "Expiry"
            elif curr_val >= coll * 2.0:
                exit_reason = "Stop Loss 2x"

            if exit_reason:
                bc    = options_buy_costs(curr_val, 1, lot)
                gross = (coll - curr_val) * lot
                net   = gross - bc - pos["sell_cost"]
                pnl_p = net / (coll * lot) * 100
                trades.append({
                    "strategy": "T2 Strangle",
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "strike_ce": pos["strike_ce"], "strike_pe": pos["strike_pe"],
                    "expiry": pos["expiry"],
                    "premium_collected": round(coll,2),
                    "premium_at_exit": round(curr_val,2),
                    "lot_size": lot, "gross_pnl": round(gross,2),
                    "costs": round(bc+pos["sell_cost"],2),
                    "net_pnl": round(net,2), "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                })
                cash += (coll - curr_val) * lot - bc
                pos = None

        if pos is None and cur_d.weekday() == 0:
            expiry    = get_next_expiry(cur_d, weekly=True)
            dte       = get_dte(cur_d, expiry)
            strike_ce = round_to_strike(spot * 1.01)
            strike_pe = round_to_strike(spot * 0.99)
            prem_ce   = bs_price(spot, strike_ce, dte, RISK_FREE, iv, "CE")
            prem_pe   = bs_price(spot, strike_pe, dte, RISK_FREE, iv, "PE")
            total_prem= prem_ce + prem_pe
            sc        = options_sell_costs(total_prem, 1, lot)
            margin    = short_options_margin(spot, lot) * 2

            vix_val = row.get("vix", 15)
            if pd.isna(vix_val) or vix_val == 0:
                vix_val = 15

            if float(vix_val) > 12 and cash > margin + 10000 and total_prem > 30:
                pos = {
                    "strike_ce": strike_ce, "strike_pe": strike_pe,
                    "expiry": expiry, "premium_collected": total_prem,
                    "entry_date": cur_d, "sell_cost": sc,
                    "iv_entry": iv, "regime": regime,
                }
                cash += total_prem * lot - sc

        curr_val = 0
        if pos:
            dte = get_dte(cur_d, pos["expiry"])
            curr_val = (bs_price(spot, pos["strike_ce"], dte, RISK_FREE, iv, "CE") +
                        bs_price(spot, pos["strike_pe"], dte, RISK_FREE, iv, "PE")) * lot
        port = cash - curr_val
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── T3: Iron Condor ───────────────────────────────────────────────────────────
def run_t3_iron_condor(df: pd.DataFrame) -> tuple[list, list]:
    """
    Sell 1% OTM strangle + Buy 2% OTM wings for protection.
    Net premium = strangle premium - wing cost.
    Max loss = wing spread - net premium.
    """
    trades, equity = [], []
    cash = float(INITIAL_CAP)
    pos  = None

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        if cur_d < WEEKLY_EXPIRY_START:
            if i % 5 == 0:
                equity.append({"date": cur_d, "value": cash, "regime": regime})
            continue

        if pos:
            dte      = get_dte(cur_d, pos["expiry"])
            # Short legs (sold)
            val_sce  = bs_price(spot, pos["strike_sce"], dte, RISK_FREE, iv, "CE")
            val_spe  = bs_price(spot, pos["strike_spe"], dte, RISK_FREE, iv, "PE")
            # Long legs (bought)
            val_lce  = bs_price(spot, pos["strike_lce"], dte, RISK_FREE, iv, "CE")
            val_lpe  = bs_price(spot, pos["strike_lpe"], dte, RISK_FREE, iv, "PE")

            curr_cost = val_sce + val_spe - val_lce - val_lpe  # net debit to close
            net_prem  = pos["net_premium"]

            exit_reason = None
            if dte <= 0 or cur_d >= pos["expiry"]:
                exit_reason = "Expiry"
            elif curr_cost >= net_prem * 3:
                exit_reason = "Stop Loss 3x"

            if exit_reason:
                gross = (net_prem - curr_cost) * lot
                tc    = options_buy_costs(curr_cost, 1, lot) + pos["entry_cost"]
                net   = gross - tc
                pnl_p = net / (net_prem * lot) * 100 if net_prem > 0 else 0
                trades.append({
                    "strategy": "T3 Iron Condor",
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "expiry": pos["expiry"],
                    "net_premium": round(net_prem,2),
                    "cost_at_exit": round(curr_cost,2),
                    "lot_size": lot, "gross_pnl": round(gross,2),
                    "costs": round(tc,2), "net_pnl": round(net,2),
                    "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                    "max_loss": round(pos["max_loss"],2),
                })
                cash += (net_prem - curr_cost) * lot - options_buy_costs(curr_cost, 1, lot)
                pos = None

        if pos is None and cur_d.weekday() == 0:
            expiry  = get_next_expiry(cur_d, weekly=True)
            dte     = get_dte(cur_d, expiry)
            sce     = round_to_strike(spot * 1.01)   # sell CE 1% OTM
            spe     = round_to_strike(spot * 0.99)   # sell PE 1% OTM
            lce     = round_to_strike(spot * 1.02)   # buy  CE 2% OTM
            lpe     = round_to_strike(spot * 0.98)   # buy  PE 2% OTM

            p_sce = bs_price(spot, sce, dte, RISK_FREE, iv, "CE")
            p_spe = bs_price(spot, spe, dte, RISK_FREE, iv, "PE")
            p_lce = bs_price(spot, lce, dte, RISK_FREE, iv, "CE")
            p_lpe = bs_price(spot, lpe, dte, RISK_FREE, iv, "PE")
            net_prem = p_sce + p_spe - p_lce - p_lpe

            if net_prem <= 10:
                if i % 5 == 0:
                    equity.append({"date": cur_d, "value": cash, "regime": regime})
                continue

            spread   = (sce - spe) / 2
            max_loss = (spread - net_prem) * lot
            ec       = options_sell_costs(p_sce + p_spe, 1, lot) + \
                       options_buy_costs(p_lce + p_lpe, 1, lot)

            if cash > abs(max_loss) * 1.5 + 10000:
                pos = {
                    "strike_sce": sce, "strike_spe": spe,
                    "strike_lce": lce, "strike_lpe": lpe,
                    "expiry": expiry, "net_premium": net_prem,
                    "entry_date": cur_d, "entry_cost": ec,
                    "max_loss": max_loss, "iv_entry": iv, "regime": regime,
                }
                cash += net_prem * lot - ec

        port = cash
        if pos:
            dte = get_dte(cur_d, pos["expiry"])
            cv  = (bs_price(spot, pos["strike_sce"], dte, RISK_FREE, iv, "CE") +
                   bs_price(spot, pos["strike_spe"], dte, RISK_FREE, iv, "PE") -
                   bs_price(spot, pos["strike_lce"], dte, RISK_FREE, iv, "CE") -
                   bs_price(spot, pos["strike_lpe"], dte, RISK_FREE, iv, "PE"))
            port = cash - cv * lot
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── S1: Bull Call Spread ──────────────────────────────────────────────────────
def run_s1_bull_call_spread(df: pd.DataFrame) -> tuple[list, list]:
    """
    Bullish signal: buy ATM CE + sell 2% OTM CE.
    Max profit = spread - debit. Max loss = debit paid.
    Exit: near expiry or when 80% of max profit achieved or 50% loss.
    """
    trades, equity = [], []
    cash = float(INITIAL_CAP)
    pos  = None

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        bull = (float(row["sma50"]) > float(row["sma200"]) and
                float(row["rsi"]) > 55 and float(row["macd_hist"]) > 0)

        if pos:
            dte    = get_dte(cur_d, pos["expiry"])
            val_long  = bs_price(spot, pos["strike_long"],  dte, RISK_FREE, iv, "CE")
            val_short = bs_price(spot, pos["strike_short"], dte, RISK_FREE, iv, "CE")
            curr_val  = val_long - val_short
            debit     = pos["debit"]
            max_profit= pos["max_profit"]

            pnl_pct = (curr_val - debit) / debit * 100 if debit > 0 else 0

            exit_reason = None
            if dte <= 2/365:
                exit_reason = "Near Expiry"
            elif pnl_pct >= 80:
                exit_reason = "Target 80%"
            elif pnl_pct <= -50:
                exit_reason = "Stop Loss 50%"
            elif not bull:
                exit_reason = "Signal Exit"

            if exit_reason:
                tc    = options_sell_costs(val_long, 1, lot) + options_buy_costs(val_short, 1, lot)
                gross = (curr_val - debit) * lot
                net   = gross - tc - pos["entry_cost"]
                pnl_p = net / (debit * lot) * 100
                trades.append({
                    "strategy": "S1 Bull Call Spread",
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "strike_long": pos["strike_long"], "strike_short": pos["strike_short"],
                    "expiry": pos["expiry"], "debit_paid": round(debit,2),
                    "exit_value": round(curr_val,2), "lot_size": lot,
                    "gross_pnl": round(gross,2), "costs": round(tc+pos["entry_cost"],2),
                    "net_pnl": round(net,2), "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                })
                cash += curr_val * lot - tc
                pos = None

        if pos is None and bull:
            expiry  = get_next_expiry(cur_d,
                        weekly=(cur_d >= WEEKLY_EXPIRY_START))
            dte     = get_dte(cur_d, expiry)
            sl      = round_to_strike(spot)           # long leg ATM
            ss      = round_to_strike(spot * 1.02)    # short leg 2% OTM
            p_long  = bs_price(spot, sl, dte, RISK_FREE, iv, "CE")
            p_short = bs_price(spot, ss, dte, RISK_FREE, iv, "CE")
            debit   = p_long - p_short
            max_p   = (ss - sl) - debit
            ec      = options_buy_costs(p_long, 1, lot) + options_sell_costs(p_short, 1, lot)

            if debit > 0 and cash > debit * lot + ec + 10000:
                pos = {
                    "strike_long": sl, "strike_short": ss, "expiry": expiry,
                    "debit": debit, "max_profit": max_p,
                    "entry_date": cur_d, "entry_cost": ec,
                    "iv_entry": iv, "regime": regime,
                }
                cash -= (debit * lot + ec)

        curr_spread = 0
        if pos:
            dte = get_dte(cur_d, pos["expiry"])
            curr_spread = (bs_price(spot, pos["strike_long"], dte, RISK_FREE, iv, "CE") -
                           bs_price(spot, pos["strike_short"], dte, RISK_FREE, iv, "CE")) * lot
        port = cash + curr_spread
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ── S2: Bear Put Spread ───────────────────────────────────────────────────────
def run_s2_bear_put_spread(df: pd.DataFrame) -> tuple[list, list]:
    """
    Bearish signal: buy ATM PE + sell 2% OTM PE.
    Defined risk bear play.
    """
    trades, equity = [], []
    cash = float(INITIAL_CAP)
    pos  = None

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        cur_d = row.name.date() if hasattr(row.name, 'date') else date.fromisoformat(str(row.name)[:10])
        spot  = float(row["close"])
        lot   = get_lot_size(cur_d)
        iv    = get_iv(row, float(row["hvol"]))
        regime = get_regime(row)

        bear = (float(row["sma50"]) < float(row["sma200"]) and
                float(row["rsi"]) < 45 and float(row["macd_hist"]) < 0)

        if pos:
            dte      = get_dte(cur_d, pos["expiry"])
            val_long  = bs_price(spot, pos["strike_long"],  dte, RISK_FREE, iv, "PE")
            val_short = bs_price(spot, pos["strike_short"], dte, RISK_FREE, iv, "PE")
            curr_val  = val_long - val_short
            debit     = pos["debit"]
            pnl_pct   = (curr_val - debit) / debit * 100 if debit > 0 else 0

            exit_reason = None
            if dte <= 2/365:
                exit_reason = "Near Expiry"
            elif pnl_pct >= 80:
                exit_reason = "Target 80%"
            elif pnl_pct <= -50:
                exit_reason = "Stop Loss 50%"
            elif not bear:
                exit_reason = "Signal Exit"

            if exit_reason:
                tc    = options_sell_costs(val_long, 1, lot) + options_buy_costs(val_short, 1, lot)
                gross = (curr_val - debit) * lot
                net   = gross - tc - pos["entry_cost"]
                pnl_p = net / (debit * lot) * 100
                trades.append({
                    "strategy": "S2 Bear Put Spread",
                    "entry_date": pos["entry_date"], "exit_date": cur_d,
                    "strike_long": pos["strike_long"], "strike_short": pos["strike_short"],
                    "expiry": pos["expiry"], "debit_paid": round(debit,2),
                    "exit_value": round(curr_val,2), "lot_size": lot,
                    "gross_pnl": round(gross,2), "costs": round(tc+pos["entry_cost"],2),
                    "net_pnl": round(net,2), "pnl_pct": round(pnl_p,2),
                    "reason": exit_reason, "regime": pos["regime"],
                    "duration": (cur_d - pos["entry_date"]).days,
                    "iv_entry": round(pos["iv_entry"]*100,1),
                })
                cash += curr_val * lot - tc
                pos = None

        if pos is None and bear:
            expiry  = get_next_expiry(cur_d, weekly=(cur_d >= WEEKLY_EXPIRY_START))
            dte     = get_dte(cur_d, expiry)
            sl      = round_to_strike(spot)
            ss      = round_to_strike(spot * 0.98)
            p_long  = bs_price(spot, sl, dte, RISK_FREE, iv, "PE")
            p_short = bs_price(spot, ss, dte, RISK_FREE, iv, "PE")
            debit   = p_long - p_short
            ec      = options_buy_costs(p_long, 1, lot) + options_sell_costs(p_short, 1, lot)

            if debit > 0 and cash > debit * lot + ec + 10000:
                pos = {
                    "strike_long": sl, "strike_short": ss, "expiry": expiry,
                    "debit": debit, "entry_date": cur_d, "entry_cost": ec,
                    "iv_entry": iv, "regime": regime,
                }
                cash -= (debit * lot + ec)

        curr_spread = 0
        if pos:
            dte = get_dte(cur_d, pos["expiry"])
            curr_spread = (bs_price(spot, pos["strike_long"], dte, RISK_FREE, iv, "PE") -
                           bs_price(spot, pos["strike_short"], dte, RISK_FREE, iv, "PE")) * lot
        port = cash + curr_spread
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": max(port, 0), "regime": regime})

    return trades, equity


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
STRATEGY_REGISTRY = {
    "F1": ("F1 Trend Futures",          run_f1_trend_futures),
    "F2": ("F2 MACD Futures",           run_f2_macd_futures),
    "D1w":("D1 ATM Weekly Buy",         lambda df: run_d1_atm_buy(df, weekly=True)),
    "D1m":("D1 ATM Monthly Buy",        lambda df: run_d1_atm_buy(df, weekly=False)),
    "T1": ("T1 ATM Straddle",           run_t1_straddle),
    "T2": ("T2 Strangle",               run_t2_strangle),
    "T3": ("T3 Iron Condor",            run_t3_iron_condor),
    "S1": ("S1 Bull Call Spread",       run_s1_bull_call_spread),
    "S2": ("S2 Bear Put Spread",        run_s2_bear_put_spread),
}

# ─────────────────────────────────────────────────────────────────────────────
# RESULTS PRINTING
# ─────────────────────────────────────────────────────────────────────────────
def _rc(val, good, ok, fmt=".1f"):
    if not RICH:
        return str(round(val, 2))
    if val >= good: return f"[bold green]{val:{fmt}}[/]"
    if val >= ok:   return f"[yellow]{val:{fmt}}[/]"
    return f"[red]{val:{fmt}}[/]"

def print_results(all_metrics: list, top_n: int = 0):
    ranked = sorted(all_metrics, key=lambda x: -x["sharpe"])
    if top_n:
        ranked = ranked[:top_n]

    if not RICH:
        print(f"\n{'─'*110}")
        print(f"{'#':>3}  {'Strategy':<28} {'CAGR%':>6} {'Sharpe':>7} {'MaxDD%':>7} "
              f"{'WR%':>6} {'PF':>5} {'Trades':>7} {'Costs₹':>9}  {'Bull WR':>8} {'Bear WR':>8}")
        print(f"{'─'*110}")
        for i, m in enumerate(ranked, 1):
            rs = m["regime_stats"]
            print(f"{i:>3}  {m['strategy']:<28} {m['cagr']:>6.1f} {m['sharpe']:>7.2f} "
                  f"{m['max_dd']:>7.1f} {m['win_rate']:>6.1f} {m['profit_factor']:>5.2f} "
                  f"{m['total_trades']:>7} {m['total_costs']:>9,.0f}  "
                  f"{rs['bull']['win_rate']:>7}% {rs['bear']['win_rate']:>7}%")
        print(f"{'─'*110}")
        return

    table = Table(title="[bold cyan]◈ NIFTY F&O BACKTEST — Ranked by Sharpe[/]",
                  header_style="bold blue", border_style="dim cyan", show_lines=False)
    for col, j in [("#","right"),("Strategy","left"),("CAGR%","right"),
                   ("Sharpe","right"),("MaxDD%","right"),("Win%","right"),
                   ("PF","right"),("Calmar","right"),("Trades","right"),
                   ("AvgDur","right"),("Costs₹","right"),
                   ("Bull WR%","right"),("Bear WR%","right"),("Side WR%","right")]:
        table.add_column(col, justify=j, no_wrap=True)

    for i, m in enumerate(ranked, 1):
        rs    = m["regime_stats"]
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else str(i)
        table.add_row(
            medal, f"[bold]{m['strategy']}[/]",
            _rc(m["cagr"],        15,   5, ".1f"),
            _rc(m["sharpe"],     1.0, 0.3, ".2f"),
            f"[{'red' if m['max_dd']>30 else 'yellow' if m['max_dd']>15 else 'green'}]{m['max_dd']:.1f}[/]",
            _rc(m["win_rate"],    60,  50, ".1f"),
            _rc(m["profit_factor"],1.5,1.1,".2f"),
            _rc(m["calmar"],     0.5, 0.2, ".2f"),
            str(m["total_trades"]),
            f"{m['avg_duration']:.0f}d",
            f"₹{m['total_costs']:,.0f}",
            f"[green]{rs['bull']['win_rate']}[/]",
            f"[{'red' if rs['bear']['win_rate']<40 else 'yellow'}]{rs['bear']['win_rate']}[/]",
            f"[yellow]{rs['sideways']['win_rate']}[/]",
        )
    console.print(table)

# ─────────────────────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────────────────────
def save_results(all_metrics: list, all_trades: list) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if all_metrics:
        rows = []
        for m in sorted(all_metrics, key=lambda x: -x["sharpe"]):
            row = {k: v for k, v in m.items()
                   if k not in ("regime_stats","annual_returns","exit_breakdown")}
            for reg in ["bull","bear","sideways"]:
                row[f"{reg}_wr"]  = m["regime_stats"][reg]["win_rate"]
                row[f"{reg}_pnl"] = m["regime_stats"][reg]["avg_pnl"]
            rows.append(row)
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"fo_summary_{ts}.csv", index=False)
    if all_trades:
        pd.DataFrame(all_trades).to_csv(RESULTS_DIR / f"fo_trades_{ts}.csv", index=False)
    with open(RESULTS_DIR / f"fo_report_{ts}.json", "w") as f:
        json.dump({"generated": ts, "metrics": all_metrics}, f, indent=2, default=str)
    return ts

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Nifty F&O Backtester")
    parser.add_argument("--strategy",    help="Run single strategy (F1/F2/D1w/D1m/T1/T2/T3/S1/S2)")
    parser.add_argument("--from",        dest="start", default=START_DATE, help="Start date YYYY-MM-DD")
    parser.add_argument("--top",         type=int, default=0)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    args = parser.parse_args()

    if args.clear_cache:
        cache = CACHE_DIR / "nifty_vix_merged.parquet"
        if cache.exists():
            cache.unlink()
        print("Cache cleared.")

    if RICH:
        console.print(Panel(
            f"[bold cyan]◈  NIFTY F&O BACKTESTER[/]\n"
            f"Period: [bold]{args.start} → {END_DATE}[/]  ·  Capital: [bold]₹{INITIAL_CAP:,}[/]\n"
            f"Pricing: [bold]Black-Scholes + India VIX[/]  ·  Lot size: [bold]historical[/]",
            border_style="blue", padding=(0,2)
        ))

    print("Fetching Nifty + VIX data...")
    df = fetch_nifty_vix(args.start, END_DATE)
    if df is None:
        print("ERROR: Could not fetch data.")
        sys.exit(1)

    # Filter by start date
    df = df[df.index >= pd.Timestamp(args.start)]
    print(f"  {len(df)} trading days loaded ({args.start} → {END_DATE})")

    vix_coverage = df["vix"].notna().sum()
    print(f"  VIX data: {vix_coverage} days ({vix_coverage/len(df)*100:.0f}% coverage)")
    print(f"  Historical vol fallback: {len(df)-vix_coverage} days\n")

    strategies = (
        {args.strategy: STRATEGY_REGISTRY[args.strategy]}
        if args.strategy and args.strategy in STRATEGY_REGISTRY
        else STRATEGY_REGISTRY
    )

    all_metrics, all_trades = [], []

    for key, (name, fn) in strategies.items():
        print(f"  Running {name}...")
        try:
            trades, equity = fn(df.copy())
            m = calc_metrics(trades, equity, name)
            if m:
                all_metrics.append(m)
                all_trades.extend(trades)
                print(f"    ✓ {len(trades)} trades | CAGR {m['cagr']}% | Sharpe {m['sharpe']} | MaxDD {m['max_dd']}%")
            else:
                print(f"    ⚠ Too few trades — skipped")
        except Exception as e:
            print(f"    ✗ Error: {e}")
            import traceback; traceback.print_exc()

    if not all_metrics:
        print("\nNo valid results.")
        return

    print()
    print_results(all_metrics, top_n=args.top)

    ts = save_results(all_metrics, all_trades)
    if RICH:
        console.print(f"\n[green]✅ Saved → results/fo_summary_{ts}.csv[/]")
    else:
        print(f"\nSaved: results/fo_summary_{ts}.csv")

    best = sorted(all_metrics, key=lambda x: -x["sharpe"])[0]

    if not args.no_telegram:
        top3 = sorted(all_metrics, key=lambda x: -x["sharpe"])[:3]
        msg  = (
            f"📊 *Nifty F&O Backtest Complete*\n"
            f"_{args.start} → {END_DATE}_\n\n"
        )
        for i, m in enumerate(top3, 1):
            rs = m["regime_stats"]
            msg += (
                f"*{i}. {m['strategy']}*\n"
                f"CAGR: `{m['cagr']}%` | Sharpe: `{m['sharpe']}` | "
                f"MaxDD: `{m['max_dd']}%`\n"
                f"WR: `{m['win_rate']}%` | Trades: `{m['total_trades']}`\n"
                f"🐂 `{rs['bull']['win_rate']}%` 🐻 `{rs['bear']['win_rate']}%` "
                f"↔ `{rs['sideways']['win_rate']}%`\n\n"
            )
        send_telegram(msg)

    if RICH:
        console.print(Panel(
            f"[bold]Winner[/]: [cyan]{best['strategy']}[/]\n"
            f"CAGR [green]{best['cagr']}%[/] · "
            f"Sharpe [cyan]{best['sharpe']}[/] · "
            f"MaxDD [red]{best['max_dd']}%[/] · "
            f"Win Rate {best['win_rate']}% · "
            f"Trades {best['total_trades']}",
            title="[bold green]◈  COMPLETE[/]", border_style="green"
        ))

if __name__ == "__main__":
    main()
