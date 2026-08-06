#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║          STRATEGY VALIDATOR — Real NSE Data Backtester               ║
║  20 Years · 3 Indices · 50 Nifty Stocks · Real Costs · Slippage     ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
  python strategy_validator.py                          # All strategies × all instruments
  python strategy_validator.py --strategy "Golden Cross"
  python strategy_validator.py --symbol RELIANCE.NS
  python strategy_validator.py --indices               # Indices only
  python strategy_validator.py --stocks                # Nifty50 stocks only
  python strategy_validator.py --top 10                # Show top 10 by Sharpe
  python strategy_validator.py --no-ai --no-telegram   # Skip optional services
  python strategy_validator.py --clear-cache           # Re-download fresh data
"""

import os, sys, json, warnings, argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Optional: rich for beautiful terminal output ──────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None
    print("💡 Tip: pip install rich  — for coloured terminal output\n")

# ─────────────────────────────────────────────────────────────────────────────
# PATHS & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CACHE_DIR   = BASE_DIR / "data_cache"
RESULTS_DIR = BASE_DIR / "results"
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

START_DATE      = "2004-01-01"
END_DATE        = datetime.today().strftime("%Y-%m-%d")
INITIAL_CAPITAL = 100_000     # ₹1,00,000
POSITION_SIZE   = 0.95        # Deploy 95% of cash per trade
CACHE_DAYS      = 1           # Re-fetch if cache is older than N days
WARMUP_BARS     = 210         # Bars before backtesting starts (for SMA200)

# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION COSTS — NSE Equity Delivery (Zerodha, 2025)
# Source: zerodha.com/charges  (last verified March 2025)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Charge                Rate          Applied on
#  ─────────────────────────────────────────────────────────────────────────
#  Brokerage             ₹0            Free for delivery at Zerodha
#  STT                   0.1%          Both buy side AND sell side value
#  NSE Transaction       0.00297%      Both sides (turnover)
#  SEBI charges          0.0001%       Both sides (₹10 per crore)
#  GST                   18%           On (brokerage + transaction charges)
#                                      Since brokerage=0, GST on txn charges
#  Stamp duty            0.015%        Buy side ONLY
#  DP charges            ₹15.34 flat   Per sell transaction (CDSL debit)
#
#  Slippage              0.05%         Buy slightly above, sell slightly below
#                                      signal price (market impact / spread)
#
#  Typical round-trip cost on ₹1L trade ≈ ₹237 (0.237%)
#  ─────────────────────────────────────────────────────────────────────────

TC = {
    "brokerage_pct":    0.0,          # ₹0 for delivery
    "stt_pct":          0.001,        # 0.1% — both sides
    "nse_txn_pct":      0.0000297,    # 0.00297% — both sides
    "sebi_pct":         0.000001,     # 0.0001% — both sides (₹10/crore)
    "gst_rate":         0.18,         # 18% on (brokerage + txn charges)
    "stamp_duty_pct":   0.00015,      # 0.015% — buy side only
    "dp_flat":          15.34,        # ₹15.34 flat per sell (CDSL)
    "slippage_pct":     0.0005,       # 0.05% each way (spread/impact)
}

def calc_trade_costs(buy_value: float, sell_value: float) -> dict:
    """
    Returns itemised transaction costs for one round-trip delivery trade.
    buy_value  = shares × buy_price   (after slippage)
    sell_value = shares × sell_price  (after slippage)
    """
    brokerage   = 0.0                                    # ₹0 delivery

    # STT — 0.1% on buy AND sell
    stt_buy     = TC["stt_pct"] * buy_value
    stt_sell    = TC["stt_pct"] * sell_value
    stt         = stt_buy + stt_sell

    # NSE Transaction charges — 0.00297% both sides
    txn_buy     = TC["nse_txn_pct"] * buy_value
    txn_sell    = TC["nse_txn_pct"] * sell_value
    txn         = txn_buy + txn_sell

    # SEBI — 0.0001% both sides
    sebi        = TC["sebi_pct"] * (buy_value + sell_value)

    # GST — 18% on (brokerage + txn charges)  — brokerage=0 here
    gst         = TC["gst_rate"] * (brokerage + txn)

    # Stamp duty — 0.015% on buy side only
    stamp       = TC["stamp_duty_pct"] * buy_value

    # DP charges — flat ₹15.34 per sell transaction
    dp          = TC["dp_flat"]

    total       = brokerage + stt + txn + sebi + gst + stamp + dp

    return {
        "brokerage": round(brokerage, 2),
        "stt":       round(stt, 2),
        "txn":       round(txn, 2),
        "sebi":      round(sebi, 2),
        "gst":       round(gst, 4),
        "stamp":     round(stamp, 2),
        "dp":        round(dp, 2),
        "total":     round(total, 2),
        "pct_of_buy":round(total / buy_value * 100, 3) if buy_value else 0,
    }

# ── Credentials (from env vars — same pattern as your ETF bot) ────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENTS
# ─────────────────────────────────────────────────────────────────────────────
INDICES = {
    "Nifty 50":         "^NSEI",
    "Bank Nifty":       "^NSEBANK",
    "Nifty Midcap 100": "^CNXMIDCAP",
}

NIFTY50_STOCKS = {
    "Reliance":      "RELIANCE.NS",
    "TCS":           "TCS.NS",
    "HDFC Bank":     "HDFCBANK.NS",
    "Infosys":       "INFY.NS",
    "ICICI Bank":    "ICICIBANK.NS",
    "HUL":           "HINDUNILVR.NS",
    "ITC":           "ITC.NS",
    "SBI":           "SBIN.NS",
    "Bharti Airtel": "BHARTIARTL.NS",
    "Kotak Bank":    "KOTAKBANK.NS",
    "L&T":           "LT.NS",
    "Axis Bank":     "AXISBANK.NS",
    "Asian Paints":  "ASIANPAINT.NS",
    "Maruti":        "MARUTI.NS",
    "Bajaj Finance": "BAJFINANCE.NS",
    "Nestle":        "NESTLEIND.NS",
    "HCL Tech":      "HCLTECH.NS",
    "Wipro":         "WIPRO.NS",
    "UltraCemco":    "ULTRACEMCO.NS",
    "ONGC":          "ONGC.NS",
    "Tata Motors":   "TATAMOTORS.NS",
    "Power Grid":    "POWERGRID.NS",
    "NTPC":          "NTPC.NS",
    "M&M":           "M&M.NS",
    "Titan":         "TITAN.NS",
    "Sun Pharma":    "SUNPHARMA.NS",
    "Tech Mahindra": "TECHM.NS",
    "Adani Ent.":    "ADANIENT.NS",
    "JSW Steel":     "JSWSTEEL.NS",
    "Tata Steel":    "TATASTEEL.NS",
    "Coal India":    "COALINDIA.NS",
    "Bajaj Finserv": "BAJAJFINSV.NS",
    "Dr. Reddy's":   "DRREDDY.NS",
    "Cipla":         "CIPLA.NS",
    "Divi's Lab":    "DIVISLAB.NS",
    "Eicher Motors": "EICHERMOT.NS",
    "Hero MotoCorp": "HEROMOTOCO.NS",
    "BPCL":          "BPCL.NS",
    "Apollo Hosp.":  "APOLLOHOSP.NS",
    "Tata Consumer": "TATACONSUM.NS",
    "Britannia":     "BRITANNIA.NS",
    "Grasim":        "GRASIM.NS",
    "Hindalco":      "HINDALCO.NS",
    "IndusInd Bank": "INDUSINDBK.NS",
    "SBI Life":      "SBILIFE.NS",
    "HDFC Life":     "HDFCLIFE.NS",
    "Bajaj Auto":    "BAJAJ-AUTO.NS",
    "UPL":           "UPL.NS",
    "Adani Ports":   "ADANIPORTS.NS",
    "Shree Cement":  "SHREECEM.NS",
}

ALL_INSTRUMENTS = {**INDICES, **NIFTY50_STOCKS}

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHER WITH DISK CACHE
# ─────────────────────────────────────────────────────────────────────────────
def fetch_data(name: str, ticker: str) -> pd.DataFrame | None:
    """
    Downloads OHLCV via yfinance, caches to parquet.
    Cache is re-used if younger than CACHE_DAYS to avoid hammering Yahoo.
    """
    safe   = ticker.replace("^", "IDX_").replace(".", "_").replace("&", "AND")
    cache  = CACHE_DIR / f"{safe}.parquet"

    # Return cached if fresh
    if cache.exists():
        age_days = (datetime.now() - datetime.fromtimestamp(cache.stat().st_mtime)).days
        if age_days < CACHE_DAYS:
            try:
                df = pd.read_parquet(cache)
                if len(df) >= WARMUP_BARS + 50:
                    return df
            except Exception:
                pass

    try:
        raw = yf.download(ticker, start=START_DATE, end=END_DATE,
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None

        # Flatten MultiIndex columns (yfinance 0.2+)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index   = pd.to_datetime(df.index)
        df         = df.dropna()

        if len(df) < WARMUP_BARS + 50:
            return None  # Not enough history

        df.to_parquet(cache)
        return df

    except Exception as e:
        return None


def clear_cache():
    for f in CACHE_DIR.glob("*.parquet"):
        f.unlink()
    print(f"🗑  Cache cleared ({CACHE_DIR})")

# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS  (pure pandas — no TA-Lib dependency)
# ─────────────────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # ── Simple Moving Averages ────────────────────────────────
    for p in [5, 10, 20, 50, 100, 200]:
        df[f"sma{p}"] = c.rolling(p).mean()

    # ── Exponential Moving Averages ───────────────────────────
    for p in [9, 12, 21, 26, 50]:
        df[f"ema{p}"] = c.ewm(span=p, adjust=False).mean()

    # ── RSI(14) ───────────────────────────────────────────────
    delta         = c.diff()
    gain          = delta.clip(lower=0).rolling(14).mean()
    loss          = (-delta.clip(upper=0)).rolling(14).mean()
    rs            = gain / loss.replace(0, np.nan)
    df["rsi"]     = 100 - (100 / (1 + rs))

    # ── MACD (12, 26, 9) ──────────────────────────────────────
    df["macd"]        = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # ── Bollinger Bands (20, 2σ) ──────────────────────────────
    bb_mid          = c.rolling(20).mean()
    bb_std          = c.rolling(20).std()
    df["bb_mid"]    = bb_mid
    df["bb_upper"]  = bb_mid + 2 * bb_std
    df["bb_lower"]  = bb_mid - 2 * bb_std
    df["bb_pct"]    = (c - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)

    # ── ATR(14) ───────────────────────────────────────────────
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()

    # ── Stochastic (14, 3) ────────────────────────────────────
    lo14          = l.rolling(14).min()
    hi14          = h.rolling(14).max()
    df["stoch_k"] = 100 * (c - lo14) / (hi14 - lo14 + 1e-9)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ── ADX + DI (14) ─────────────────────────────────────────
    up_move     = h.diff()
    down_move   = -l.diff()
    plus_dm     = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm    = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr14_s     = pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean()  # proxy
    plus_di     = 100 * pd.Series(plus_dm, index=df.index).ewm(span=14, adjust=False).mean() / (tr.ewm(span=14, adjust=False).mean() + 1e-9)
    minus_di    = 100 * pd.Series(minus_dm, index=df.index).ewm(span=14, adjust=False).mean() / (tr.ewm(span=14, adjust=False).mean() + 1e-9)
    dx          = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    df["adx"]       = dx.ewm(span=14, adjust=False).mean()
    df["plus_di"]   = plus_di
    df["minus_di"]  = minus_di

    # ── Volume MA(20) ─────────────────────────────────────────
    df["vol_ma"] = v.rolling(20).mean()

    # ── 52-week rolling high ──────────────────────────────────
    df["high_52w"] = c.rolling(252).max()

    return df.dropna(subset=["sma200", "rsi", "macd", "adx"])

# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTOR  (based on actual price + MA structure)
# ─────────────────────────────────────────────────────────────────────────────
def detect_regime(df: pd.DataFrame) -> pd.Series:
    """
    Bull     → price > SMA200 AND SMA50 > SMA200
    Bear     → price < SMA200 AND SMA50 < SMA200
    Sideways → everything else (transitioning / choppy)
    """
    bull     = (df["close"] > df["sma200"]) & (df["sma50"] > df["sma200"])
    bear     = (df["close"] < df["sma200"]) & (df["sma50"] < df["sma200"])
    regime   = pd.Series("sideways", index=df.index, dtype=str)
    regime[bull] = "bull"
    regime[bear] = "bear"
    return regime

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE  (bar-by-bar state machine)
# ─────────────────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, strategy: dict) -> dict:
    """
    Simulates single-position trading bar by bar.
    Handles intra-bar stop-loss / take-profit / trailing-stop correctly.
    Returns trade list + equity curve.
    """
    entry_fn  = strategy["entry"]
    exit_fn   = strategy["exit"]
    sl        = strategy.get("stop_loss")       # e.g. 0.07 → 7% below entry
    tp        = strategy.get("take_profit")     # e.g. 0.20 → 20% above entry
    ts        = strategy.get("trailing_stop")   # e.g. 0.09 → 9% below peak
    max_hold  = strategy.get("max_hold_days", 500)

    # Pre-compute boolean signal Series
    try:
        entry_sig = entry_fn(df).fillna(False)
        exit_sig  = exit_fn(df).fillna(False)
    except Exception as e:
        return {"trades": [], "equity": pd.DataFrame()}

    regime = detect_regime(df)

    # State
    cash        = float(INITIAL_CAPITAL)
    shares      = 0
    entry_price = 0.0
    entry_date  = None
    entry_idx   = 0
    peak_price  = 0.0
    trades      = []
    equity_rows = []

    dates = df.index.tolist()

    for i, date in enumerate(dates):
        if i < WARMUP_BARS:
            equity_rows.append({"date": date, "value": cash, "regime": "sideways"})
            continue

        row      = df.loc[date]
        port_val = cash + shares * row["close"]
        equity_rows.append({"date": date, "value": port_val, "regime": regime.loc[date]})

        # ── IN POSITION: check exits ──────────────────────────
        if shares > 0:
            peak_price  = max(peak_price, row["high"])
            exit_reason = None

            if max_hold and (i - entry_idx) >= max_hold:
                exit_reason = "MaxHold"
            elif sl and row["low"] <= entry_price * (1 - sl):
                exit_reason = "StopLoss"
            elif tp and row["high"] >= entry_price * (1 + tp):
                exit_reason = "TakeProfit"
            elif ts and row["low"] <= peak_price * (1 - ts):
                exit_reason = "TrailingStop"
            elif bool(exit_sig.loc[date]):
                exit_reason = "Signal"

            if exit_reason:
                # ── Raw exit price (intra-bar logic) ──────────
                ep_raw = (
                    max(row["low"], entry_price * (1 - sl)) if exit_reason == "StopLoss"    else
                    min(row["high"], entry_price * (1 + tp)) if exit_reason == "TakeProfit" else
                    max(row["low"], peak_price * (1 - ts))  if exit_reason == "TrailingStop" else
                    float(row["close"])
                )
                ep_raw = max(ep_raw, 0.01)

                # ── Slippage: sell slightly BELOW signal price ─
                # Simulates market impact, spread, and imperfect fills.
                # Stop-loss exits get worse slippage (panic selling).
                slip_mult = 1.5 if exit_reason == "StopLoss" else 1.0
                ep = ep_raw * (1 - TC["slippage_pct"] * slip_mult)

                # ── Real transaction costs ─────────────────────
                buy_value  = shares * entry_price          # already paid at entry
                sell_value = shares * ep
                costs      = calc_trade_costs(buy_value, sell_value)
                # Note: buy-side costs (STT, stamp, slippage) were already
                # deducted at entry. Here we only deduct sell-side costs
                # plus the buy-side costs we held back.
                # Easier approach: deduct full round-trip at exit.
                total_cost = costs["total"]

                gross_pnl  = shares * (ep - entry_price)
                net_pnl    = gross_pnl - total_cost
                net_pnl_pct = net_pnl / (shares * entry_price) * 100

                # Dominant regime during trade
                t_regimes  = regime.loc[entry_date:date]
                dom_regime = t_regimes.value_counts().idxmax() if len(t_regimes) else "unknown"

                trades.append({
                    "strategy":    strategy.get("name", ""),
                    "instrument":  "",   # filled by caller
                    "entry_date":  entry_date,
                    "exit_date":   date,
                    "entry_price": round(entry_price, 2),
                    "exit_price":  round(ep, 2),
                    "shares":      shares,
                    "gross_pnl":   round(gross_pnl, 2),
                    "costs":       round(total_cost, 2),
                    "pnl":         round(net_pnl, 2),         # NET of all costs
                    "pnl_pct":     round(net_pnl_pct, 2),     # NET %
                    "cost_stt":    costs["stt"],
                    "cost_txn":    costs["txn"],
                    "cost_dp":     costs["dp"],
                    "cost_stamp":  costs["stamp"],
                    "cost_gst":    costs["gst"],
                    "reason":      exit_reason,
                    "regime":      dom_regime,
                    "duration":    (date - entry_date).days,
                })
                cash       += shares * ep - total_cost
                shares      = 0
                entry_price = 0.0
                peak_price  = 0.0

        # ── FLAT: check entry ─────────────────────────────────
        if shares == 0 and bool(entry_sig.loc[date]):
            # ── Slippage: buy slightly ABOVE signal price ──────
            raw_price  = float(row["close"])
            exec_price = raw_price * (1 + TC["slippage_pct"])  # 0.05% worse

            buyable = int(cash * POSITION_SIZE / exec_price)
            if buyable > 0:
                shares      = buyable
                entry_price = exec_price          # store slippage-adjusted price
                entry_date  = date
                entry_idx   = i
                peak_price  = float(row["high"])
                cash       -= shares * exec_price

    eq_df = pd.DataFrame(equity_rows).set_index("date")
    return {"trades": trades, "equity": eq_df}

# ─────────────────────────────────────────────────────────────────────────────
# METRICS CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────
def calc_metrics(trades: list, equity: pd.DataFrame,
                 strategy_name: str, instrument: str) -> dict | None:

    if len(trades) < 3 or equity.empty:
        return None

    eq_vals = equity["value"].astype(float)
    init    = float(INITIAL_CAPITAL)
    final   = float(eq_vals.iloc[-1])
    years   = max((equity.index[-1] - equity.index[0]).days / 365.25, 0.5)

    if final <= 0:
        return None

    cagr      = ((final / init) ** (1 / years) - 1) * 100
    total_ret = (final - init) / init * 100

    # Max Drawdown
    rolling_max = eq_vals.cummax()
    dd_series   = (eq_vals - rolling_max) / rolling_max * 100
    max_dd      = abs(float(dd_series.min()))

    # Sharpe (annualised daily returns, 252 trading days)
    daily_ret = eq_vals.pct_change().dropna()
    std_d     = daily_ret.std()
    sharpe    = float(daily_ret.mean() / std_d * np.sqrt(252)) if std_d > 1e-9 else 0.0

    calmar = cagr / max_dd if max_dd > 0 else 0.0

    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    win_rate      = len(wins) / len(trades) * 100
    avg_win       = float(np.mean([t["pnl_pct"] for t in wins]))   if wins   else 0.0
    avg_loss      = float(np.mean([t["pnl_pct"] for t in losses])) if losses else 0.0
    gross_win     = sum(t["pnl"] for t in wins)
    gross_loss    = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 999.0
    avg_duration  = float(np.mean([t["duration"] for t in trades]))
    expectancy    = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # ── Cost analysis ─────────────────────────────────────────────────────────
    total_costs    = sum(t.get("costs", 0) for t in trades)
    total_stt      = sum(t.get("cost_stt", 0) for t in trades)
    total_dp       = sum(t.get("cost_dp", 0) for t in trades)
    total_stamp    = sum(t.get("cost_stamp", 0) for t in trades)
    total_txn      = sum(t.get("cost_txn", 0) for t in trades)
    total_gst      = sum(t.get("cost_gst", 0) for t in trades)
    avg_cost_pct   = float(np.mean([t.get("costs", 0) / max(abs(t["shares"] * t["entry_price"]), 1) * 100
                                    for t in trades]))
    # Gross CAGR (what it would have been without any costs)
    gross_final    = final + total_costs
    gross_cagr     = ((gross_final / init) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    cagr_drag      = gross_cagr - cagr   # how many % points costs eat per year

    # Regime breakdown
    regime_stats = {}
    for reg in ["bull", "bear", "sideways"]:
        rt = [t for t in trades if t["regime"] == reg]
        rw = [t for t in rt if t["pnl"] > 0]
        regime_stats[reg] = {
            "trades":    len(rt),
            "win_rate":  round(len(rw) / len(rt) * 100, 1) if rt else 0.0,
            "avg_pnl":   round(float(np.mean([t["pnl_pct"] for t in rt])), 2) if rt else 0.0,
            "total_pnl": round(sum(t["pnl"] for t in rt), 0),
        }

    # Annual P&L
    annual = {}
    for t in trades:
        yr = str(t["exit_date"])[:4]
        annual[yr] = round(annual.get(yr, 0.0) + t["pnl"], 0)

    # Exit breakdown
    exit_bkdn = {}
    for t in trades:
        exit_bkdn[t["reason"]] = exit_bkdn.get(t["reason"], 0) + 1

    return {
        "strategy":      strategy_name,
        "instrument":    instrument,
        # ── Net performance (after all costs + slippage) ──────
        "cagr":          round(cagr, 2),
        "total_return":  round(total_ret, 2),
        "final_value":   round(final, 0),
        "max_dd":        round(max_dd, 2),
        "sharpe":        round(sharpe, 2),
        "calmar":        round(calmar, 2),
        "win_rate":      round(win_rate, 1),
        "total_trades":  len(trades),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "profit_factor": round(min(profit_factor, 99.0), 2),
        "avg_duration":  round(avg_duration, 0),
        "expectancy":    round(expectancy, 2),
        "years":         round(years, 1),
        # ── Cost breakdown ────────────────────────────────────
        "gross_cagr":    round(gross_cagr, 2),   # CAGR before costs
        "cagr_drag":     round(cagr_drag, 2),    # % pts eaten by costs/yr
        "total_costs":   round(total_costs, 0),  # total ₹ paid in costs
        "total_stt":     round(total_stt, 0),
        "total_dp":      round(total_dp, 0),
        "total_stamp":   round(total_stamp, 0),
        "total_txn":     round(total_txn, 0),
        "total_gst":     round(total_gst, 0),
        "avg_cost_pct":  round(avg_cost_pct, 3), # avg cost per trade as % of trade value
        # ── Breakdown ─────────────────────────────────────────
        "regime_stats":  regime_stats,
        "annual_returns": annual,
        "exit_breakdown": exit_bkdn,
    }

# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL REPORTER
# ─────────────────────────────────────────────────────────────────────────────
def _rc(val: float, good: float, ok: float, fmt: str = ".1f") -> str:
    """Return rich color-coded string."""
    if   val >= good: return f"[bold green]{val:{fmt}}[/]"
    elif val >= ok:   return f"[yellow]{val:{fmt}}[/]"
    else:             return f"[red]{val:{fmt}}[/]"

def print_header(n_instruments: int, n_strategies: int):
    msg = (
        f"[bold cyan]◈  STRATEGY VALIDATOR — REAL NSE DATA[/]\n"
        f"Instruments: [bold]{n_instruments}[/]  ·  Strategies: [bold]{n_strategies}[/]  ·  "
        f"Period: [bold]{START_DATE} → {END_DATE}[/]  ·  Capital: [bold]₹{INITIAL_CAPITAL:,}[/]"
    )
    if RICH:
        console.print(Panel(msg, border_style="blue", padding=(0, 2)))
    else:
        print(f"\n◈  STRATEGY VALIDATOR  |  {n_instruments} instruments × {n_strategies} strategies")
        print(f"   Period: {START_DATE} → {END_DATE}  |  Capital: ₹{INITIAL_CAPITAL:,}\n")

def print_summary_table(metrics_list: list, top_n: int = 0):
    ranked = sorted(metrics_list, key=lambda x: -x["sharpe"])
    if top_n:
        ranked = ranked[:top_n]

    if not RICH:
        # Plain-text fallback
        w = 160
        print("\n" + "─" * w)
        hdr = (f"{'#':>3}  {'Strategy':<26} {'Instrument':<16}  "
               f"{'CAGR%':>6} {'Sharpe':>7} {'MaxDD%':>7} {'WR%':>6} "
               f"{'PF':>5} {'Calmar':>7} {'Trades':>7} "
               f"{'Bull WR':>8} {'Bear WR':>8} {'Side WR':>8}")
        print(hdr)
        print("─" * w)
        for i, m in enumerate(ranked, 1):
            rs = m["regime_stats"]
            print(f"{i:>3}  {m['strategy']:<26} {m['instrument']:<16}  "
                  f"{m['cagr']:>6.1f} {m['sharpe']:>7.2f} {m['max_dd']:>7.1f} "
                  f"{m['win_rate']:>6.1f} {m['profit_factor']:>5.2f} {m['calmar']:>7.2f} "
                  f"{m['total_trades']:>7} "
                  f"{rs['bull']['win_rate']:>7}% {rs['bear']['win_rate']:>7}% {rs['sideways']['win_rate']:>7}%")
        print("─" * w)
        return

    table = Table(
        title="[bold cyan]◈ RESULTS — Ranked by Sharpe Ratio[/]",
        header_style="bold blue",
        border_style="dim cyan",
        show_lines=False,
        min_width=160,
    )
    cols = [
        ("#",         "right"), ("Strategy",   "left"),  ("Instrument",  "left"),
        ("CAGR%",     "right"), ("Sharpe",     "right"), ("MaxDD%",      "right"),
        ("Win%",      "right"), ("PF",         "right"), ("Calmar",      "right"),
        ("Trades",    "right"), ("AvgDur(d)",  "right"),
        ("Bull WR%",  "right"), ("Bear WR%",   "right"), ("Side WR%",    "right"),
    ]
    for c, j in cols:
        table.add_column(c, justify=j, no_wrap=True)

    for i, m in enumerate(ranked, 1):
        rs      = m["regime_stats"]
        medal   = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else str(i)
        table.add_row(
            medal,
            f"[bold]{m['strategy']}[/]",
            m["instrument"],
            _rc(m["cagr"],          15,   8,  ".1f"),
            _rc(m["sharpe"],       1.5, 0.8,  ".2f"),
            f"[{'red' if m['max_dd']>25 else 'yellow' if m['max_dd']>12 else 'green'}]{m['max_dd']:.1f}[/]",
            _rc(m["win_rate"],      55,  45,  ".1f"),
            _rc(m["profit_factor"],1.8, 1.2,  ".2f"),
            _rc(m["calmar"],       0.8, 0.4,  ".2f"),
            str(m["total_trades"]),
            f"{m['avg_duration']:.0f}",
            f"[green]{rs['bull']['win_rate']}[/]",
            f"[{'red' if rs['bear']['win_rate']<40 else 'yellow'}]{rs['bear']['win_rate']}[/]",
            f"[yellow]{rs['sideways']['win_rate']}[/]",
        )
    console.print(table)

def print_detail_card(m: dict):
    if not RICH:
        return
    rs = m["regime_stats"]
    ann = m.get("annual_returns", {})
    best_yr  = max(ann, key=ann.get, default="N/A")
    worst_yr = min(ann, key=ann.get, default="N/A")
    pos_yrs  = sum(1 for v in ann.values() if v > 0)
    neg_yrs  = sum(1 for v in ann.values() if v <= 0)

    content = (
        f"[bold cyan]{m['strategy']}[/]  ×  [bold]{m['instrument']}[/]"
        f"  |  {m['years']} yrs  |  {m['total_trades']} trades\n\n"

        f"[bold]Returns[/]:    "
        f"CAGR [green]{m['cagr']}%[/]  ·  Total [green]{m['total_return']}%[/]"
        f"  ·  Final ₹[bold]{m['final_value']:,.0f}[/]\n"

        f"[bold]Risk[/]:       "
        f"MaxDD [red]{m['max_dd']}%[/]  ·  Sharpe [cyan]{m['sharpe']}[/]"
        f"  ·  Calmar [cyan]{m['calmar']}[/]\n"

        f"[bold]Edge[/]:       "
        f"Win Rate {m['win_rate']}%  ·  PF {m['profit_factor']}"
        f"  ·  Expectancy {m['expectancy']}%/trade"
        f"  ·  Avg Win [green]{m['avg_win']}%[/]  ·  Avg Loss [red]{m['avg_loss']}%[/]\n"

        f"[bold]Duration[/]:   Avg hold {m['avg_duration']:.0f} days"
        f"  ·  Exits: {m['exit_breakdown']}\n\n"

        f"[bold]Cost Impact (20yr total)[/]:\n"
        f"  Gross CAGR (no costs):  [dim]{m['gross_cagr']}%[/]\n"
        f"  Net CAGR (after costs): [cyan]{m['cagr']}%[/]\n"
        f"  CAGR drag from costs:   [yellow]{m['cagr_drag']}% pts/yr[/]\n"
        f"  Total costs paid:       ₹{m['total_costs']:,.0f}\n"
        f"    STT:      ₹{m['total_stt']:,.0f}  ·  "
        f"DP: ₹{m['total_dp']:,.0f}  ·  "
        f"Stamp: ₹{m['total_stamp']:,.0f}  ·  "
        f"Txn+SEBI: ₹{m['total_txn']:,.0f}  ·  "
        f"GST: ₹{m['total_gst']:,.0f}\n"
        f"  Avg cost/trade:         {m['avg_cost_pct']}% of trade value\n\n"

        f"[bold]Regime Breakdown[/]:\n"
        f"  🐂 Bull:     {rs['bull']['trades']:>4} trades  "
        f"{rs['bull']['win_rate']:>5}% WR  "
        f"avg [green]{rs['bull']['avg_pnl']:+.2f}%[/]/trade  "
        f"₹{rs['bull']['total_pnl']:+,.0f}\n"

        f"  🐻 Bear:     {rs['bear']['trades']:>4} trades  "
        f"{rs['bear']['win_rate']:>5}% WR  "
        f"avg [{'red' if rs['bear']['avg_pnl']<0 else 'green'}]{rs['bear']['avg_pnl']:+.2f}%[/]/trade  "
        f"₹{rs['bear']['total_pnl']:+,.0f}\n"

        f"  ↔ Sideways:  {rs['sideways']['trades']:>4} trades  "
        f"{rs['sideways']['win_rate']:>5}% WR  "
        f"avg {rs['sideways']['avg_pnl']:+.2f}%/trade  "
        f"₹{rs['sideways']['total_pnl']:+,.0f}\n\n"

        f"[bold]Annual P&L[/]:  "
        f"Best [green]{best_yr}[/]  ·  Worst [red]{worst_yr}[/]"
        f"  ·  Positive yrs [green]{pos_yrs}[/]  ·  Negative yrs [red]{neg_yrs}[/]"
    )
    console.print(Panel(content, title=f"[bold blue]DETAIL — #{list(m.keys())[0] if 'rank' in m else ''}[/]",
                        border_style="blue", padding=(0, 2)))

# ─────────────────────────────────────────────────────────────────────────────
# SAVE RESULTS
# ─────────────────────────────────────────────────────────────────────────────
def save_results(all_metrics: list, all_trades: list) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Summary CSV
    if all_metrics:
        rows = []
        for m in sorted(all_metrics, key=lambda x: -x["sharpe"]):
            row = {k: v for k, v in m.items() if k not in ("regime_stats", "annual_returns", "exit_breakdown")}
            for reg in ["bull", "bear", "sideways"]:
                row[f"{reg}_trades"] = m["regime_stats"][reg]["trades"]
                row[f"{reg}_wr"]     = m["regime_stats"][reg]["win_rate"]
                row[f"{reg}_avg_pnl"]= m["regime_stats"][reg]["avg_pnl"]
            rows.append(row)
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"summary_{ts}.csv", index=False)

    # All trades CSV
    if all_trades:
        pd.DataFrame(all_trades).sort_values(["strategy","instrument","entry_date"]).to_csv(
            RESULTS_DIR / f"trades_{ts}.csv", index=False)

    # Full JSON (for AI analysis / further processing)
    with open(RESULTS_DIR / f"report_{ts}.json", "w") as f:
        json.dump({"generated": ts, "config": {"start": START_DATE, "end": END_DATE,
                   "capital": INITIAL_CAPITAL}, "metrics": all_metrics},
                  f, indent=2, default=str)

    return ts

# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL: ANTHROPIC AI ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
def ai_analyze(all_metrics: list) -> str:
    if not ANTHROPIC_KEY:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        top5   = sorted(all_metrics, key=lambda x: -x["sharpe"])[:5]
        prompt = f"""You are an expert quantitative analyst reviewing backtested trading strategies
on NSE Indian equities (real 20-year data via Yahoo Finance, 2004-2024).

Top-5 results by Sharpe:
{json.dumps(top5, indent=2, default=str)}

Write a concise expert report covering:
1. Best strategy/instrument combo and the specific reason it outperforms.
2. What common traits separate the top performers from the bottom ones?
3. Which strategy survives bear markets best — and why?
4. Key risk: what real-world friction (slippage, gap-downs, costs) could erode these returns?
5. One concrete, testable improvement to the #1 strategy.
6. Regime verdict: which strategy is best for Indian markets specifically (hint: India has long bull runs, sharp bear crashes)?

Max 350 words. Quantitative, direct, zero fluff."""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"AI analysis error: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL: TELEGRAM SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(all_metrics: list):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT or not all_metrics:
        return
    try:
        import requests
        top = sorted(all_metrics, key=lambda x: -x["sharpe"])[:3]
        lines = [f"📊 *Strategy Validator Results*\n_{START_DATE} → {END_DATE}_\n"]
        for i, m in enumerate(top, 1):
            rs = m["regime_stats"]
            lines.append(
                f"*{i}. {m['strategy']} @ {m['instrument']}*\n"
                f"CAGR: `{m['cagr']}%` | Sharpe: `{m['sharpe']}` | MaxDD: `{m['max_dd']}%`\n"
                f"Win Rate: `{m['win_rate']}%` | PF: `{m['profit_factor']}` | Trades: `{m['total_trades']}`\n"
                f"🐂 Bull: `{rs['bull']['win_rate']}%` | "
                f"🐻 Bear: `{rs['bear']['win_rate']}%` | "
                f"↔ Side: `{rs['sideways']['win_rate']}%`\n"
            )
        msg = "\n".join(lines)
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        print("📱 Telegram summary sent.")
    except Exception as e:
        print(f"⚠ Telegram error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    from strategies_config import STRATEGIES

    parser = argparse.ArgumentParser(description="Strategy Validator — Real NSE Backtester")
    parser.add_argument("--strategy",    help="Run only this strategy (exact name)")
    parser.add_argument("--symbol",      help="Run only this symbol, e.g. RELIANCE.NS or ^NSEI")
    parser.add_argument("--indices",     action="store_true", help="Run on indices only")
    parser.add_argument("--stocks",      action="store_true", help="Run on Nifty50 stocks only")
    parser.add_argument("--top",         type=int, default=0, help="Show top N results")
    parser.add_argument("--no-ai",       action="store_true", help="Skip Anthropic AI analysis")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram notification")
    parser.add_argument("--clear-cache", action="store_true", help="Delete cached data and re-download")
    args = parser.parse_args()

    if args.clear_cache:
        clear_cache()

    # ── Select instruments ────────────────────────────────────
    if args.symbol:
        # Try to find the name from our dicts, else use ticker as name
        name = next((k for k, v in ALL_INSTRUMENTS.items() if v == args.symbol), args.symbol)
        instruments = {name: args.symbol}
    elif args.indices:
        instruments = INDICES
    elif args.stocks:
        instruments = NIFTY50_STOCKS
    else:
        instruments = ALL_INSTRUMENTS

    # ── Select strategies ─────────────────────────────────────
    if args.strategy:
        strategies = {k: v for k, v in STRATEGIES.items() if k == args.strategy}
        if not strategies:
            avail = list(STRATEGIES.keys())
            print(f"Strategy '{args.strategy}' not found.\nAvailable: {avail}")
            sys.exit(1)
    else:
        strategies = STRATEGIES

    print_header(len(instruments), len(strategies))

    total_runs  = len(instruments) * len(strategies)
    all_metrics = []
    all_trades  = []
    done        = 0
    skipped     = 0

    # ── Run all combinations ──────────────────────────────────
    if RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[bold cyan]{task.completed}/{task.total}[/]"),
            TimeRemainingColumn(),
            console=console,
        )
        task = progress.add_task("Backtesting...", total=total_runs)
        progress.start()
    else:
        progress = None

    for inst_name, ticker in instruments.items():

        if RICH:
            progress.update(task, description=f"↓ {inst_name:<20}")

        df_raw = fetch_data(inst_name, ticker)

        if df_raw is None:
            msg = f"  [yellow]⚠ {inst_name} ({ticker}): insufficient data — skipped[/]"
            if RICH: console.log(msg)
            else: print(f"  ⚠ {inst_name}: no/insufficient data — skipped")
            done += len(strategies)
            skipped += len(strategies)
            if RICH: progress.update(task, advance=len(strategies))
            continue

        df = add_indicators(df_raw.copy())

        for strat_name, strategy in strategies.items():
            done += 1
            strat_with_name = dict(strategy, name=strat_name)
            if RICH:
                progress.update(task, description=f"  {strat_name:<28} × {inst_name:<16}",
                                advance=1)
            else:
                pct = done / total_runs * 100
                print(f"  [{pct:5.1f}%] {strat_name:<28} × {inst_name}", end="\r")

            try:
                result  = run_backtest(df, strat_with_name)
                metrics = calc_metrics(result["trades"], result["equity"],
                                       strat_name, inst_name)
                if metrics:
                    all_metrics.append(metrics)
                    for t in result["trades"]:
                        t["instrument"] = inst_name
                        all_trades.append(t)
            except Exception:
                pass  # Skip silently; don't crash entire run

    if RICH:
        progress.stop()
    else:
        print(" " * 80, end="\r")

    # ── Print results ─────────────────────────────────────────
    if not all_metrics:
        print("\n❌ No valid results. Try --indices only or relax strategy conditions.")
        return

    print_summary_table(all_metrics, top_n=args.top)

    # Detailed cards for top 3
    ranked_top3 = sorted(all_metrics, key=lambda x: -x["sharpe"])[:3]
    if RICH:
        console.print("\n[bold cyan]TOP 3 — DETAILED BREAKDOWNS[/]")
        for m in ranked_top3:
            print_detail_card(m)

    # ── Save ──────────────────────────────────────────────────
    ts = save_results(all_metrics, all_trades)
    if RICH:
        console.print(f"\n[green]✅ Results saved → results/summary_{ts}.csv[/]")
        console.print(f"[green]   Trades saved  → results/trades_{ts}.csv[/]")
    else:
        print(f"\n✅  Saved: results/summary_{ts}.csv  |  results/trades_{ts}.csv")

    # ── AI Analysis ───────────────────────────────────────────
    if not args.no_ai:
        if ANTHROPIC_KEY:
            if RICH: console.print("\n[magenta]🤖 Running Anthropic AI analysis...[/]")
            else: print("\n🤖 Running AI analysis...")
            ai_text = ai_analyze(all_metrics)
            if ai_text:
                if RICH:
                    console.print(Panel(ai_text,
                        title="[bold magenta]AI ANALYSIS — ANTHROPIC[/]",
                        border_style="magenta", padding=(1, 2)))
                else:
                    print(f"\n── AI ANALYSIS ──\n{ai_text}\n{'─'*60}")
        else:
            if RICH: console.print("[dim]ℹ  Set ANTHROPIC_API_KEY env var to enable AI analysis.[/]")

    # ── Telegram ──────────────────────────────────────────────
    if not args.no_telegram:
        send_telegram(all_metrics)

    # ── Final summary line ────────────────────────────────────
    best = sorted(all_metrics, key=lambda x: -x["sharpe"])[0]
    if RICH:
        # ── Cost summary across all runs ──────────────────────────────────────
        grand_costs = sum(m["total_costs"] for m in all_metrics)
        avg_drag    = float(np.mean([m["cagr_drag"] for m in all_metrics]))
        avg_stt_pct = float(np.mean([m["total_stt"] / max(m["total_costs"],1) * 100 for m in all_metrics]))

        console.print(Panel(
            f"[bold]Transaction Cost Summary (across all {len(all_metrics)} valid combos)[/]\n"
            f"  Avg CAGR drag from costs + slippage: [yellow]{avg_drag:.2f}% pts / year[/]\n"
            f"  STT is the biggest cost: ~[red]{avg_stt_pct:.0f}%[/] of total charges\n"
            f"  Charges included: [dim]STT 0.1% both sides · NSE txn 0.00297% · "
            f"SEBI 0.0001% · Stamp 0.015% buy · DP ₹15.34/sell · Slippage 0.05% both ways[/]",
            title="[bold yellow]₹ REAL COSTS APPLIED[/]", border_style="yellow", padding=(0, 2)
        ))

        console.print(Panel(
            f"[bold]Best combo[/]: [cyan]{best['strategy']}[/] × [bold]{best['instrument']}[/]\n"
            f"Net CAGR [green]{best['cagr']}%[/]  (Gross: {best['gross_cagr']}%)"
            f"  ·  Sharpe [cyan]{best['sharpe']}[/]"
            f"  ·  MaxDD [red]{best['max_dd']}%[/]  ·  Win Rate {best['win_rate']}%\n"
            f"Valid combos: [bold]{len(all_metrics)}[/] / {total_runs - skipped}  ·  Skipped: {skipped}",
            title="[bold green]◈  RUN COMPLETE[/]", border_style="green"
        ))

if __name__ == "__main__":
    main()
