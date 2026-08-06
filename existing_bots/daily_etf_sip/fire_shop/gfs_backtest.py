#!/usr/bin/env python3
"""
gfs_backtest.py — Vishal Malkan GFS Strategy Backtester
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tests 2 strategies across all Nifty 500 instruments (or subset):

  GFS Basic:
    Monthly RSI(14) > 60
    Weekly  RSI(14) > 60
    Daily   RSI(14) in range [35, 45]
    Entry: Buy above high of the trigger candle
    Exit:  Daily RSI crosses above 60 (target reached)
           OR Daily RSI drops below 30 (stop)

  GFS Advanced:
    All GFS Basic conditions PLUS:
    Price > 50-day EMA (trend confirmation)
    Price > 200-day SMA (long-term structure)
    Volume > 1.5x 20-day avg volume (conviction)
    ADX > 20 (trend strength)
    Candle: today's close > today's open (green candle at pullback)
    Entry: Same as basic
    Exit:  RSI > 60 OR RSI < 30 OR price < 50 EMA

Usage:
  python gfs_backtest.py                         # all Nifty 50 stocks
  python gfs_backtest.py --universe nifty500     # broader universe
  python gfs_backtest.py --symbol RELIANCE.NS    # single stock
  python gfs_backtest.py --top 15                # show top 15
  python gfs_backtest.py --compare               # GFS vs BB Squeeze comparison
  python gfs_backtest.py --no-telegram
"""

import os, sys, json, warnings, argparse
from datetime import datetime, date
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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

START_DATE      = "2004-01-01"
END_DATE        = datetime.today().strftime("%Y-%m-%d")
INITIAL_CAPITAL = 100_000
POSITION_SIZE   = 0.95
WARMUP          = 250    # need enough bars for monthly RSI (20 months min)
RSI_PERIOD      = 14

BASE_DIR        = Path(__file__).parent
CACHE_DIR       = BASE_DIR / "data_cache"
RESULTS_DIR     = BASE_DIR / "results"
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENTS
# ─────────────────────────────────────────────────────────────────────────────
NIFTY50_STOCKS = {
    "Reliance":      "RELIANCE.NS",  "TCS":           "TCS.NS",
    "HDFC Bank":     "HDFCBANK.NS",  "Infosys":       "INFY.NS",
    "ICICI Bank":    "ICICIBANK.NS", "HUL":           "HINDUNILVR.NS",
    "ITC":           "ITC.NS",       "SBI":           "SBIN.NS",
    "Bharti Airtel": "BHARTIARTL.NS","Kotak Bank":    "KOTAKBANK.NS",
    "L&T":           "LT.NS",        "Axis Bank":     "AXISBANK.NS",
    "Asian Paints":  "ASIANPAINT.NS","Maruti":        "MARUTI.NS",
    "Bajaj Finance": "BAJFINANCE.NS","Nestle":        "NESTLEIND.NS",
    "HCL Tech":      "HCLTECH.NS",   "Wipro":         "WIPRO.NS",
    "UltraCemco":    "ULTRACEMCO.NS","ONGC":          "ONGC.NS",
    "Tata Motors":   "TATAMOTORS.NS","Power Grid":    "POWERGRID.NS",
    "NTPC":          "NTPC.NS",      "M&M":           "M&M.NS",
    "Titan":         "TITAN.NS",     "Sun Pharma":    "SUNPHARMA.NS",
    "Tech Mahindra": "TECHM.NS",     "Adani Ent.":    "ADANIENT.NS",
    "JSW Steel":     "JSWSTEEL.NS",  "Tata Steel":    "TATASTEEL.NS",
    "Coal India":    "COALINDIA.NS", "Bajaj Finserv": "BAJAJFINSV.NS",
    "Dr. Reddy's":   "DRREDDY.NS",   "Cipla":         "CIPLA.NS",
    "Divi's Lab":    "DIVISLAB.NS",  "Eicher Motors": "EICHERMOT.NS",
    "Hero MotoCorp": "HEROMOTOCO.NS","BPCL":          "BPCL.NS",
    "Apollo Hosp.":  "APOLLOHOSP.NS","Tata Consumer": "TATACONSUM.NS",
    "Britannia":     "BRITANNIA.NS", "Grasim":        "GRASIM.NS",
    "Hindalco":      "HINDALCO.NS",  "IndusInd Bank": "INDUSINDBK.NS",
    "SBI Life":      "SBILIFE.NS",   "HDFC Life":     "HDFCLIFE.NS",
    "Bajaj Auto":    "BAJAJ-AUTO.NS","UPL":           "UPL.NS",
    "Adani Ports":   "ADANIPORTS.NS","Shree Cement":  "SHREECEM.NS",
}

# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION COSTS
# ─────────────────────────────────────────────────────────────────────────────
TC = {
    "stt_pct":        0.001,
    "nse_txn_pct":    0.0000297,
    "sebi_pct":       0.000001,
    "gst_rate":       0.18,
    "stamp_duty_pct": 0.00015,
    "dp_flat":        15.34,
    "slippage_pct":   0.0005,
}

def calc_costs(buy_val: float, sell_val: float) -> float:
    stt   = TC["stt_pct"] * (buy_val + sell_val)
    txn   = TC["nse_txn_pct"] * (buy_val + sell_val)
    sebi  = TC["sebi_pct"] * (buy_val + sell_val)
    gst   = TC["gst_rate"] * txn
    stamp = TC["stamp_duty_pct"] * buy_val
    dp    = TC["dp_flat"]
    return stt + txn + sebi + gst + stamp + dp

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────
def fetch_data(name: str, ticker: str) -> pd.DataFrame | None:
    safe  = ticker.replace("^","IDX_").replace(".","_").replace("&","AND")
    cache = CACHE_DIR / f"{safe}.parquet"

    if cache.exists():
        age = (datetime.now() - datetime.fromtimestamp(cache.stat().st_mtime)).days
        if age < 1:
            try:
                df = pd.read_parquet(cache)
                if len(df) >= WARMUP + 50:
                    return df
            except Exception:
                pass

    try:
        raw = yf.download(ticker, start=START_DATE, end=END_DATE,
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index = pd.to_datetime(df.index)
        df = df.dropna()
        if len(df) < WARMUP + 50:
            return None
        df.to_parquet(cache)
        return df
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# RSI CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TIMEFRAME RSI BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def build_mtf_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes daily OHLCV and adds:
      - daily RSI
      - weekly RSI (resampled to weekly, forward-filled back to daily)
      - monthly RSI (resampled to monthly, forward-filled back to daily)
      - all indicators needed for GFS Advanced
    """
    # ── Daily indicators ──────────────────────────────────────────────────────
    c = df["close"]
    df["rsi_daily"] = calc_rsi(c, RSI_PERIOD)

    # SMAs and EMAs
    df["sma50"]     = c.rolling(50).mean()
    df["sma200"]    = c.rolling(200).mean()
    df["ema50"]     = c.ewm(span=50, adjust=False).mean()

    # ADX
    h, l = df["high"], df["low"]
    tr   = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    up   = h.diff();  dn = -l.diff()
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    mdm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = tr.ewm(span=14, adjust=False).mean()
    pdi  = 100 * pd.Series(pdm, index=df.index).ewm(span=14, adjust=False).mean() / atr_
    mdi  = 100 * pd.Series(mdm, index=df.index).ewm(span=14, adjust=False).mean() / atr_
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-9)
    df["adx"] = dx.ewm(span=14, adjust=False).mean()

    # Volume MA
    df["vol_ma"] = df["volume"].rolling(20).mean()

    # ATR for stop sizing
    df["atr"] = tr.ewm(span=14, adjust=False).mean()

    # ── Weekly RSI ───────────────────────────────────────────────────────────
    # Resample to weekly (Friday close), compute RSI, forward-fill to daily
    weekly_close = df["close"].resample("W-FRI").last().dropna()
    weekly_rsi   = calc_rsi(weekly_close, RSI_PERIOD)
    # Forward-fill weekly RSI to daily index
    df["rsi_weekly"] = weekly_rsi.reindex(df.index, method="ffill")

    # ── Monthly RSI ──────────────────────────────────────────────────────────
    monthly_close = df["close"].resample("ME").last().dropna()
    monthly_rsi   = calc_rsi(monthly_close, RSI_PERIOD)
    df["rsi_monthly"] = monthly_rsi.reindex(df.index, method="ffill")

    return df.dropna(subset=["rsi_daily", "rsi_weekly", "rsi_monthly",
                              "sma200", "ema50", "adx"])

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
# BACKTEST ENGINE — shared for both GFS variants
# ─────────────────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, strategy_name: str) -> tuple[list, list]:
    """
    Entry logic differs by strategy — passed via strategy_name.
    Shared exit logic.
    """
    trades, equity = [], []
    cash             = float(INITIAL_CAPITAL)
    shares           = 0
    entry_p          = 0.0
    entry_d          = None
    entry_idx        = 0
    entry_high       = 0.0
    entry_candle_low = 0.0
    entry_reg        = "sideways"
    rsi_reset        = True   # True = RSI has been above 55 since last exit
                               # Prevents re-entering same pullback after exit

    for i in range(WARMUP, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]
        cur_d = row.name.date() if hasattr(row.name, "date") else \
                date.fromisoformat(str(row.name)[:10])

        price       = float(row["close"])
        rsi_d       = float(row["rsi_daily"])
        rsi_w       = float(row["rsi_weekly"])
        rsi_m       = float(row["rsi_monthly"])
        rsi_d_prev  = float(prev["rsi_daily"])
        vol         = float(row["volume"])
        vol_ma      = float(row["vol_ma"]) if not pd.isna(row["vol_ma"]) else vol
        adx         = float(row["adx"])
        ema50       = float(row["ema50"])
        sma200      = float(row["sma200"])
        regime      = get_regime(row)

        port_val = cash + shares * price
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": port_val, "regime": regime})

        # ── EXIT CHECK ───────────────────────────────────────────────────────
        if shares > 0:
            exit_reason = None

            # ── Trailing stop logic (Malkan: 3-5 bar trailing after RSI > 60) ─
            # Once RSI has crossed 60, we switch to a trailing stop
            # using the lowest low of the last 4 bars as the stop level
            if rsi_d_prev >= 60 or (i - entry_idx > 0 and
               df.iloc[entry_idx:i]["rsi_daily"].max() >= 60):
                # RSI has been above 60 at some point — now trailing
                trail_bars = 4   # Malkan says 3-5 bars, use 4
                trail_low  = float(df.iloc[max(0,i-trail_bars):i]["low"].min())
                if price < trail_low:
                    exit_reason = "Trailing Stop (4-bar low)"

            # Primary exit — RSI crosses above 60 on daily (Malkan exact rule)
            if not exit_reason and rsi_d >= 60 and rsi_d_prev < 60:
                exit_reason = "RSI Target 60"

            # Hard stop — below LOW of the signal/alert candle (Malkan exact)
            if not exit_reason and price < entry_candle_low:
                exit_reason = "Stop Loss (candle low)"

            # Weekly RSI breaks below 50 — father trend broken
            if not exit_reason and rsi_w < 50 and float(prev["rsi_weekly"]) >= 50:
                exit_reason = "Weekly RSI < 50"

            # Advanced only — price breaks below 50 EMA
            if not exit_reason and strategy_name == "GFS Advanced" and price < ema50:
                exit_reason = "Price < 50 EMA"

            # Max hold 180 days — positional strategy
            if not exit_reason and (i - entry_idx) >= 180:
                exit_reason = "Max Hold 180d"

            if exit_reason:
                ep        = price * (1 - TC["slippage_pct"])
                gross     = shares * (ep - entry_p)
                costs     = calc_costs(shares * entry_p, shares * ep)
                net       = gross - costs
                pnl_pct   = net / (shares * entry_p) * 100

                # Dominant regime during trade
                t_reg   = df.iloc[entry_idx:i+1].apply(get_regime, axis=1)
                dom_reg = t_reg.value_counts().idxmax() if len(t_reg) else "unknown"

                trades.append({
                    "strategy":    strategy_name,
                    "entry_date":  entry_d,
                    "exit_date":   cur_d,
                    "entry_price": round(entry_p, 2),
                    "exit_price":  round(ep, 2),
                    "shares":      shares,
                    "gross_pnl":   round(gross, 2),
                    "costs":       round(costs, 2),
                    "net_pnl":     round(net, 2),
                    "pnl_pct":     round(pnl_pct, 2),
                    "reason":      exit_reason,
                    "regime":      dom_reg,
                    "duration":    (cur_d - entry_d).days,
                    "rsi_entry_d": round(float(df.iloc[entry_idx]["rsi_daily"]), 1),
                    "rsi_exit_d":  round(rsi_d, 1),
                    "rsi_weekly":  round(rsi_w, 1),
                    "rsi_monthly": round(rsi_m, 1),
                    "signal_candle_high": round(entry_high, 2),
                    "signal_candle_low":  round(entry_candle_low, 2),
                })
                cash             += shares * ep - costs
                shares            = 0
                entry_p           = 0.0
                entry_high        = 0.0
                entry_candle_low  = 0.0
                rsi_reset         = False   # must see RSI > 55 before next entry

        # ── ENTRY CHECK ──────────────────────────────────────────────────────
        if shares == 0:

            # Track RSI reset — must see RSI > 55 after last exit
            # Confirms pullback cycle completed before allowing new entry
            if not rsi_reset and rsi_d > 55:
                rsi_reset = True

            # ── GFS Basic conditions (Vishal Malkan transcript exact) ─────────
            # Monthly RSI(14) > 60  — Grandfather bullish
            # Weekly  RSI(14) > 60  — Father bullish
            # Daily   RSI(14) near 40 (35-45 zone) — Son pulling back
            # PLUS: today must be a bullish (green) candle — alert candle
            gfs_basic = (
                rsi_m > 60 and
                rsi_w > 60 and
                rsi_d >= 35 and
                rsi_d < 45 and
                float(row["close"]) > float(row["open"])   # bullish alert candle
            )

            # ── GFS Advanced additional filters ──────────────────────────────
            # Volume note: pullbacks on LOW volume = healthy (no panic selling)
            # Volume on pullback day should be below average — that's the signal
            # High volume on a pullback = distribution/selling = avoid
            gfs_advanced = gfs_basic and (
                price > ema50 and           # price above 50 EMA
                price > sma200 and          # above 200 SMA (Stage 2)
                adx > 20 and                # trend has strength
                vol < vol_ma * 1.0          # low/normal volume on pullback (healthy)
            )

            condition = (gfs_advanced if strategy_name == "GFS Advanced"
                         else gfs_basic)

            if condition and rsi_reset:
                # ── Entry: above HIGH of the alert/signal candle ──────────────
                # Transcript: "entry is made above its high"
                # Signal candle = today. Entry = above today's high.
                # We simulate this as: entry at today's high + slippage
                # (in live trading you place a buy-stop above the candle high)
                exec_price = float(row["high"]) * (1 + TC["slippage_pct"])

                buyable = int(cash * POSITION_SIZE / exec_price)
                if buyable > 0 and cash > exec_price * buyable:
                    shares           = buyable
                    entry_p          = exec_price
                    entry_d          = cur_d
                    entry_idx        = i
                    entry_high       = float(row["high"])
                    # Stop loss: below LOW of signal candle (Malkan exact)
                    entry_candle_low = float(row["low"]) * (1 - TC["slippage_pct"])
                    entry_reg        = regime
                    cash            -= shares * exec_price

    return trades, equity

# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────
def calc_metrics(trades: list, equity: list,
                 strategy: str, instrument: str) -> dict | None:
    if len(trades) < 3 or not equity:
        return None

    eq_vals = [e["value"] for e in equity]
    init    = float(INITIAL_CAPITAL)
    final   = float(eq_vals[-1])
    years   = max((equity[-1]["date"] - equity[0]["date"]).days / 365.25, 0.5)

    if final <= 0:
        return None

    cagr      = ((final / init) ** (1 / years) - 1) * 100
    total_ret = (final - init) / init * 100

    peak = init;  max_dd = 0.0
    for v in eq_vals:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    rets   = [(eq_vals[i]-eq_vals[i-1])/eq_vals[i-1] for i in range(1,len(eq_vals))]
    mr, sr = np.mean(rets), np.std(rets)
    sharpe = (mr / sr * np.sqrt(252)) if sr > 1e-9 else 0.0
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    wr     = len(wins) / len(trades) * 100
    aw     = np.mean([t["pnl_pct"] for t in wins])   if wins   else 0.0
    al     = np.mean([t["pnl_pct"] for t in losses]) if losses else 0.0
    gw     = sum(t["net_pnl"] for t in wins)
    gl     = abs(sum(t["net_pnl"] for t in losses))
    pf     = gw / gl if gl > 0 else 99.0
    dur    = np.mean([t["duration"] for t in trades])
    costs  = sum(t["costs"] for t in trades)
    exp    = (wr/100 * aw) + ((1-wr/100) * al)

    # Regime stats
    rs = {}
    for reg in ["bull","bear","sideways"]:
        rt = [t for t in trades if t["regime"] == reg]
        rw = [t for t in rt if t["net_pnl"] > 0]
        rs[reg] = {
            "trades":    len(rt),
            "win_rate":  round(len(rw)/len(rt)*100, 1) if rt else 0.0,
            "avg_pnl":   round(float(np.mean([t["pnl_pct"] for t in rt])), 2) if rt else 0.0,
            "total_pnl": round(sum(t["net_pnl"] for t in rt), 0),
        }

    # RSI quality stats (unique to GFS)
    rsi_entries = [t["rsi_entry_d"] for t in trades if "rsi_entry_d" in t]
    avg_rsi_entry = round(np.mean(rsi_entries), 1) if rsi_entries else 0.0

    annual = {}
    for t in trades:
        yr = str(t["exit_date"])[:4]
        annual[yr] = round(annual.get(yr, 0.0) + t["net_pnl"], 0)

    exit_bkdn = {}
    for t in trades:
        exit_bkdn[t["reason"]] = exit_bkdn.get(t["reason"], 0) + 1

    return {
        "strategy":        strategy,
        "instrument":      instrument,
        "cagr":            round(cagr, 2),
        "total_return":    round(total_ret, 2),
        "final_value":     round(final, 0),
        "max_dd":          round(max_dd, 2),
        "sharpe":          round(sharpe, 2),
        "calmar":          round(calmar, 2),
        "win_rate":        round(wr, 1),
        "total_trades":    len(trades),
        "avg_win":         round(aw, 2),
        "avg_loss":        round(al, 2),
        "profit_factor":   round(min(pf, 99.0), 2),
        "avg_duration":    round(dur, 1),
        "total_costs":     round(costs, 0),
        "expectancy":      round(exp, 2),
        "avg_rsi_entry":   avg_rsi_entry,
        "years":           round(years, 1),
        "regime_stats":    rs,
        "annual_returns":  annual,
        "exit_breakdown":  exit_bkdn,
    }

# ─────────────────────────────────────────────────────────────────────────────
# PRINT RESULTS
# ─────────────────────────────────────────────────────────────────────────────
def _rc(val, good, ok, fmt=".1f"):
    if not RICH: return str(round(val, 2))
    if val >= good: return f"[bold green]{val:{fmt}}[/]"
    if val >= ok:   return f"[yellow]{val:{fmt}}[/]"
    return f"[red]{val:{fmt}}[/]"

def print_results(all_metrics: list, top_n: int = 0, title: str = "GFS Backtest Results"):
    ranked = sorted(all_metrics, key=lambda x: -x["sharpe"])
    if top_n: ranked = ranked[:top_n]

    if not RICH:
        w = 155
        print(f"\n{'─'*w}")
        print(f"{'#':>3}  {'Strategy':<18} {'Instrument':<16} "
              f"{'CAGR%':>6} {'Sharpe':>7} {'MaxDD%':>7} {'WR%':>6} "
              f"{'PF':>5} {'Trades':>7} {'AvgRSI':>7} "
              f"{'Bull WR':>8} {'Bear WR':>8}")
        print(f"{'─'*w}")
        for i, m in enumerate(ranked, 1):
            rs = m["regime_stats"]
            print(f"{i:>3}  {m['strategy']:<18} {m['instrument']:<16} "
                  f"{m['cagr']:>6.1f} {m['sharpe']:>7.2f} {m['max_dd']:>7.1f} "
                  f"{m['win_rate']:>6.1f} {m['profit_factor']:>5.2f} "
                  f"{m['total_trades']:>7} {m['avg_rsi_entry']:>7.1f} "
                  f"{rs['bull']['win_rate']:>7}% {rs['bear']['win_rate']:>7}%")
        print(f"{'─'*w}")
        return

    table = Table(
        title=f"[bold cyan]◈ {title} — Ranked by Sharpe[/]",
        header_style="bold blue", border_style="dim cyan", show_lines=False
    )
    for col, j in [
        ("#","right"), ("Strategy","left"), ("Instrument","left"),
        ("CAGR%","right"), ("Sharpe","right"), ("MaxDD%","right"),
        ("Win%","right"), ("PF","right"), ("Calmar","right"),
        ("Trades","right"), ("AvgDur","right"), ("AvgRSI@Entry","right"),
        ("Bull WR%","right"), ("Bear WR%","right"), ("Side WR%","right"),
    ]:
        table.add_column(col, justify=j, no_wrap=True)

    for i, m in enumerate(ranked, 1):
        rs    = m["regime_stats"]
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else str(i)
        table.add_row(
            medal,
            f"[bold]{'🔵' if 'Advanced' in m['strategy'] else '⚪'} {m['strategy']}[/]",
            m["instrument"],
            _rc(m["cagr"],        15,   8, ".1f"),
            _rc(m["sharpe"],     1.5, 0.8, ".2f"),
            f"[{'red' if m['max_dd']>25 else 'yellow' if m['max_dd']>12 else 'green'}]{m['max_dd']:.1f}[/]",
            _rc(m["win_rate"],    58,  48, ".1f"),
            _rc(m["profit_factor"],1.8,1.2,".2f"),
            _rc(m["calmar"],     0.8, 0.4, ".2f"),
            str(m["total_trades"]),
            f"{m['avg_duration']:.0f}d",
            f"[cyan]{m['avg_rsi_entry']:.1f}[/]",
            f"[green]{rs['bull']['win_rate']}[/]",
            f"[{'red' if rs['bear']['win_rate']<40 else 'yellow'}]{rs['bear']['win_rate']}[/]",
            f"[yellow]{rs['sideways']['win_rate']}[/]",
        )
    if RICH: console.print(table)

def print_comparison(gfs_metrics: list, bb_metrics: list):
    """Side-by-side comparison: GFS vs BB Squeeze (from earlier backtest)."""
    if not RICH:
        return

    # Average metrics by strategy type
    def avg_m(mlist, key):
        vals = [m[key] for m in mlist if m[key] is not None]
        return round(np.mean(vals), 2) if vals else 0

    strategies = {}
    for m in gfs_metrics + bb_metrics:
        s = m["strategy"]
        if s not in strategies:
            strategies[s] = []
        strategies[s].append(m)

    rows = []
    for s, mlist in strategies.items():
        rows.append({
            "strategy":    s,
            "avg_cagr":    avg_m(mlist, "cagr"),
            "avg_sharpe":  avg_m(mlist, "sharpe"),
            "avg_dd":      avg_m(mlist, "max_dd"),
            "avg_wr":      avg_m(mlist, "win_rate"),
            "avg_pf":      avg_m(mlist, "profit_factor"),
            "count":       len(mlist),
            "positive":    sum(1 for m in mlist if m["cagr"] > 0),
        })

    table = Table(
        title="[bold magenta]◈ GFS vs BB Squeeze — Average Performance[/]",
        header_style="bold magenta", border_style="dim magenta"
    )
    for col, j in [("Strategy","left"),("Count","right"),
                   ("Avg CAGR%","right"),("Avg Sharpe","right"),
                   ("Avg MaxDD%","right"),("Avg Win%","right"),
                   ("Avg PF","right"),("Positive%","right")]:
        table.add_column(col, justify=j)

    for r in sorted(rows, key=lambda x: -x["avg_sharpe"]):
        pos_pct = round(r["positive"]/r["count"]*100, 0) if r["count"] else 0
        table.add_row(
            f"[bold]{r['strategy']}[/]",
            str(r["count"]),
            _rc(r["avg_cagr"],  12, 6, ".1f"),
            _rc(r["avg_sharpe"],1.2,0.5,".2f"),
            f"[{'red' if r['avg_dd']>20 else 'yellow'}]{r['avg_dd']:.1f}[/]",
            _rc(r["avg_wr"],   55, 48, ".1f"),
            _rc(r["avg_pf"],  1.6,1.2, ".2f"),
            f"{'[green]' if pos_pct>60 else '[yellow]'}{pos_pct:.0f}%[/]",
        )
    console.print(table)

# ─────────────────────────────────────────────────────────────────────────────
# SAVE RESULTS
# ─────────────────────────────────────────────────────────────────────────────
def save_results(all_metrics: list, all_trades: list, prefix: str = "gfs") -> str:
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
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"{prefix}_summary_{ts}.csv", index=False)
    if all_trades:
        pd.DataFrame(all_trades).to_csv(RESULTS_DIR / f"{prefix}_trades_{ts}.csv", index=False)
    with open(RESULTS_DIR / f"{prefix}_report_{ts}.json", "w") as f:
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
    parser = argparse.ArgumentParser(description="GFS Strategy Backtester")
    parser.add_argument("--symbol",       help="Single symbol e.g. RELIANCE.NS")
    parser.add_argument("--top",          type=int, default=0)
    parser.add_argument("--compare",      action="store_true",
                        help="Compare GFS vs BB Squeeze results")
    parser.add_argument("--no-telegram",  action="store_true")
    parser.add_argument("--clear-cache",  action="store_true")
    parser.add_argument("--advanced-only",action="store_true",
                        help="Run GFS Advanced only (skip basic)")
    args = parser.parse_args()

    if args.clear_cache:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        print("Cache cleared.")

    instruments = (
        {args.symbol.split(".")[0]: args.symbol}
        if args.symbol
        else NIFTY50_STOCKS
    )

    strategies = (
        ["GFS Advanced"]
        if args.advanced_only
        else ["GFS Basic", "GFS Advanced"]
    )

    if RICH:
        console.print(Panel(
            f"[bold cyan]◈  GFS BACKTESTER — Vishal Malkan Framework[/]\n"
            f"Instruments: [bold]{len(instruments)}[/]  ·  "
            f"Strategies: [bold]{len(strategies)}[/]  ·  "
            f"Period: [bold]{START_DATE} → {END_DATE}[/]\n"
            f"Rules: Monthly RSI>60 · Weekly RSI>60 · Daily RSI 35–45\n"
            f"Advanced adds: Price>50EMA · Price>200SMA · ADX>20 · Volume>1.2x · Green candle",
            border_style="blue", padding=(0, 2)
        ))

    total_runs  = len(instruments) * len(strategies)
    all_metrics = []
    all_trades  = []
    done        = 0

    if RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}[/]"),
            TimeRemainingColumn(),
            console=console,
        )
        task = progress.add_task("Running GFS backtest...", total=total_runs)
        progress.start()

    for inst_name, ticker in instruments.items():
        if RICH:
            progress.update(task, description=f"↓ {inst_name:<20}")

        df_raw = fetch_data(inst_name, ticker)
        if df_raw is None:
            done += len(strategies)
            if RICH: progress.update(task, advance=len(strategies))
            continue

        try:
            df = build_mtf_dataframe(df_raw.copy())
        except Exception as e:
            print(f"  MTF build failed for {inst_name}: {e}")
            done += len(strategies)
            if RICH: progress.update(task, advance=len(strategies))
            continue

        for strat in strategies:
            done += 1
            if RICH:
                progress.update(task, description=f"  {strat:<20} × {inst_name:<16}",
                                advance=1)
            else:
                print(f"  [{done/total_runs*100:5.1f}%] {strat} × {inst_name}", end="\r")

            try:
                trades, equity = run_backtest(df, strat)
                m = calc_metrics(trades, equity, strat, inst_name)
                if m:
                    all_metrics.append(m)
                    for t in trades:
                        t["instrument"] = inst_name
                        all_trades.append(t)
            except Exception as e:
                pass

    if RICH: progress.stop()
    else:    print(" " * 80, end="\r")

    if not all_metrics:
        print("No valid results generated.")
        return

    print_results(all_metrics, top_n=args.top)

    # Load previous BB Squeeze results for comparison
    if args.compare:
        bb_files = sorted(RESULTS_DIR.glob("summary_*.csv"), reverse=True)
        if bb_files:
            bb_df = pd.read_csv(bb_files[0])
            bb_sq = bb_df[bb_df["strategy"] == "BB Squeeze Breakout"].to_dict("records")
            if bb_sq:
                print_comparison(all_metrics, bb_sq)
            else:
                if RICH: console.print("[yellow]No BB Squeeze results found for comparison[/]")
        else:
            if RICH: console.print("[yellow]No previous backtest results found for comparison[/]")

    ts = save_results(all_metrics, all_trades)

    if RICH:
        console.print(f"\n[green]✅ Saved → results/gfs_summary_{ts}.csv[/]")

    # Summary stats
    basic_m    = [m for m in all_metrics if m["strategy"] == "GFS Basic"]
    advanced_m = [m for m in all_metrics if m["strategy"] == "GFS Advanced"]

    best = sorted(all_metrics, key=lambda x: -x["sharpe"])[0]

    if RICH:
        def avg(lst, k): return round(np.mean([m[k] for m in lst]), 2) if lst else 0
        console.print(Panel(
            f"[bold]GFS Basic[/]    ({len(basic_m)} combos):  "
            f"Avg CAGR [green]{avg(basic_m,'cagr')}%[/]  ·  "
            f"Avg Sharpe [cyan]{avg(basic_m,'sharpe')}[/]  ·  "
            f"Avg WR {avg(basic_m,'win_rate')}%\n"
            f"[bold]GFS Advanced[/] ({len(advanced_m)} combos):  "
            f"Avg CAGR [green]{avg(advanced_m,'cagr')}%[/]  ·  "
            f"Avg Sharpe [cyan]{avg(advanced_m,'sharpe')}[/]  ·  "
            f"Avg WR {avg(advanced_m,'win_rate')}%\n\n"
            f"[bold]Best single combo[/]: [cyan]{best['strategy']}[/] × [bold]{best['instrument']}[/]\n"
            f"CAGR [green]{best['cagr']}%[/]  ·  "
            f"Sharpe [cyan]{best['sharpe']}[/]  ·  "
            f"MaxDD [red]{best['max_dd']}%[/]  ·  "
            f"Avg RSI at entry [cyan]{best['avg_rsi_entry']}[/]",
            title="[bold green]◈  GFS COMPLETE[/]", border_style="green"
        ))

    if not args.no_telegram and all_metrics:
        top3 = sorted(all_metrics, key=lambda x: -x["sharpe"])[:3]
        msg  = "📊 *GFS Backtest — Vishal Malkan*\n"
        msg += f"_Monthly>60 · Weekly>60 · Daily 35-45_\n\n"
        for i, m in enumerate(top3, 1):
            rs = m["regime_stats"]
            msg += (
                f"*{i}. {m['strategy']} × {m['instrument']}*\n"
                f"CAGR: `{m['cagr']}%` | Sharpe: `{m['sharpe']}` | "
                f"MaxDD: `{m['max_dd']}%` | WR: `{m['win_rate']}%`\n"
                f"Avg RSI @ entry: `{m['avg_rsi_entry']}`\n\n"
            )
        send_telegram(msg)

if __name__ == "__main__":
    main()
