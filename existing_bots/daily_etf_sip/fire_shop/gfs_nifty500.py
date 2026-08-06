#!/usr/bin/env python3
"""
gfs_nifty500.py — GFS Backtest on Full Nifty 500 Universe
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Automatically downloads the current Nifty 500 constituent list from NSE,
then runs GFS Basic + Advanced backtest on all 500 stocks.

This is the right universe for GFS — Vishal Malkan scans 500+ stocks
weekly to find the 2-3 that are in the RSI 35-45 pullback zone with
monthly + weekly RSI > 60. With only 50 stocks you get too few signals.

Usage:
  python gfs_nifty500.py                     # full run (takes 45-60 min)
  python gfs_nifty500.py --top 30            # show top 30 by Sharpe
  python gfs_nifty500.py --advanced-only     # GFS Advanced only
  python gfs_nifty500.py --min-trades 5      # filter combos with < 5 trades
  python gfs_nifty500.py --no-telegram
  python gfs_nifty500.py --clear-cache       # re-download all data

Results:
  results/gfs500_summary_YYYYMMDD.csv        # all combos ranked by Sharpe
  results/gfs500_trades_YYYYMMDD.csv         # every trade detail
  results/gfs500_screener_YYYYMMDD.csv       # today's live GFS scan
"""

import os, sys, json, time, warnings, argparse
from datetime import datetime, date
from pathlib import Path
from io import StringIO

import pandas as pd
import numpy as np
import yfinance as yf
import requests

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
WARMUP          = 250
RSI_PERIOD      = 14

BASE_DIR        = Path(__file__).parent
CACHE_DIR       = BASE_DIR / "data_cache"
RESULTS_DIR     = BASE_DIR / "results"
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# NSE index constituent CSV URLs
NSE_N500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
NSE_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.niftyindices.com/",
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
    stt   = TC["stt_pct"]   * (buy_val + sell_val)
    txn   = TC["nse_txn_pct"] * (buy_val + sell_val)
    sebi  = TC["sebi_pct"]  * (buy_val + sell_val)
    gst   = TC["gst_rate"]  * txn
    stamp = TC["stamp_duty_pct"] * buy_val
    dp    = TC["dp_flat"]
    return stt + txn + sebi + gst + stamp + dp

# ─────────────────────────────────────────────────────────────────────────────
# FETCH NIFTY 500 CONSTITUENT LIST FROM NSE
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nifty500_list() -> dict:
    """
    Downloads the official Nifty 500 list from NSE.
    Returns {company_name: yfinance_ticker} dict.
    Falls back to a hardcoded Nifty 100 list if download fails.
    """
    cache_file = CACHE_DIR / "nifty500_list.csv"

    # Use cached list if < 7 days old
    if cache_file.exists():
        age = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).days
        if age < 7:
            try:
                df = pd.read_csv(cache_file)
                result = {}
                for _, row in df.iterrows():
                    sym = str(row["Symbol"]).strip().upper()
                    name = str(row["Company Name"]).strip()
                    # Convert NSE symbol to yfinance ticker
                    yf_ticker = _nse_to_yf(sym)
                    result[name[:20]] = yf_ticker
                if len(result) >= 100:
                    print(f"  Using cached Nifty 500 list ({len(result)} stocks, {age}d old)")
                    return result
            except Exception:
                pass

    print("  Downloading Nifty 500 list from NSE...")
    try:
        resp = requests.get(NSE_N500_URL, headers=NSE_HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))

        # NSE CSV columns: "Company Name", "Industry", "Symbol", "Series", "ISIN Code"
        if "Symbol" not in df.columns:
            raise ValueError(f"Unexpected columns: {df.columns.tolist()}")

        df.to_csv(cache_file, index=False)
        print(f"  Downloaded {len(df)} stocks from NSE")

        result = {}
        for _, row in df.iterrows():
            sym      = str(row["Symbol"]).strip().upper()
            name     = str(row["Company Name"]).strip()
            yf_ticker = _nse_to_yf(sym)
            result[name[:20]] = yf_ticker

        return result

    except Exception as e:
        print(f"  NSE download failed: {e}")
        print("  Falling back to hardcoded Nifty 100 list...")
        return _fallback_nifty100()

def _nse_to_yf(symbol: str) -> str:
    """Convert NSE symbol to Yahoo Finance ticker."""
    # Handle special cases
    replacements = {
        "M&M":         "M&M.NS",
        "L&T":         "LT.NS",
        "BAJAJ-AUTO":  "BAJAJ-AUTO.NS",
        "HDFCAMC":     "HDFCAMC.NS",
    }
    if symbol in replacements:
        return replacements[symbol]
    # Standard: just add .NS
    return f"{symbol}.NS"

def _fallback_nifty100() -> dict:
    """Hardcoded Nifty 100 as fallback if NSE download fails."""
    return {
        # Nifty 50
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
        # Nifty Next 50
        "Adani Power":   "ADANIPOWER.NS","Adani Total":   "ATGL.NS",
        "Ambuja Cement": "AMBUJACEM.NS", "Avenue Super.": "DMART.NS",
        "Bajaj Holdings":"BAJAJHLDNG.NS","Bank of Baroda":"BANKBARODA.NS",
        "Berger Paints": "BERGEPAINT.NS","Bharat Electron":"BEL.NS",
        "Bharat Forge":  "BHARATFORG.NS","Bosch":         "BOSCHLTD.NS",
        "Canara Bank":   "CANBK.NS",     "Cholamandalam": "CHOLAFIN.NS",
        "Colgate":       "COLPAL.NS",    "CONCOR":        "CONCOR.NS",
        "Dabur":         "DABUR.NS",     "DLF":           "DLF.NS",
        "Godrej Consumer":"GODREJCP.NS", "Godrej Props":  "GODREJPROP.NS",
        "HAL":           "HAL.NS",       "HAVELLS":       "HAVELLS.NS",
        "HPCL":          "HINDPETRO.NS", "Info Edge":     "NAUKRI.NS",
        "IOC":           "IOC.NS",       "Jio Financial": "JIOFIN.NS",
        "LIC Housing":   "LICHSGFIN.NS", "Lupin":         "LUPIN.NS",
        "Muthoot Fin":   "MUTHOOTFIN.NS","Pidilite":      "PIDILITIND.NS",
        "Punjab Natl Bk":"PNB.NS",       "SBI Cards":     "SBICARD.NS",
        "Siemens":       "SIEMENS.NS",   "Torrent Pharma":"TORNTPHARM.NS",
        "Trent":         "TRENT.NS",     "TVS Motor":     "TVSMOTOR.NS",
        "Varun Beverages":"VBL.NS",      "Vedanta":       "VEDL.NS",
        "Zomato":        "ZOMATO.NS",    "Interglobe":    "INDIGO.NS",
        "ABB India":     "ABB.NS",       "ACC":           "ACC.NS",
        "Ashok Leyland": "ASHOKLEY.NS",  "Astral":        "ASTRAL.NS",
        "AU Small Fin":  "AUBANK.NS",    "Bandhan Bank":  "BANDHANBNK.NS",
        "CRISIL":        "CRISIL.NS",    "CG Power":      "CGPOWER.NS",
        "Cummins India": "CUMMINSIND.NS","Federal Bank":  "FEDERALBNK.NS",
        "GMR Airports":  "GMRINFRA.NS",  "GRSE":          "GRSE.NS",
        "Hindustan Zinc":"HINDZINC.NS",  "IDBI Bank":     "IDBI.NS",
        "IDFC First":    "IDFCFIRSTB.NS","Indian Hotels":  "INDHOTEL.NS",
        "Ipca Labs":     "IPCALAB.NS",   "Jubilant Food": "JUBLFOOD.NS",
        "Kansai Nerolac":"KANSAINER.NS", "LTIMindtree":   "LTIM.NS",
        "Marico":        "MARICO.NS",    "Mphasis":       "MPHASIS.NS",
        "Naukri":        "NAUKRI.NS",    "Oberoi Realty": "OBEROIRLTY.NS",
        "Oracle India":  "OFSS.NS",      "Page Industries":"PAGEIND.NS",
        "Persistent Sys":"PERSISTENT.NS","PI Industries":  "PIIND.NS",
        "Polycab":       "POLYCAB.NS",   "Prestige Est.": "PRESTIGE.NS",
        "Solar Inds":    "SOLARINDS.NS", "Supreme Inds":  "SUPREMEIND.NS",
        "Torrent Power": "TORNTPOWER.NS","Tata Chemicals": "TATACHEM.NS",
        "Tata Comm":     "TATACOMM.NS",  "Tata Elxsi":    "TATAELXSI.NS",
        "Tata Power":    "TATAPOWER.NS", "Tube Inves":    "TIINDIA.NS",
        "United Breweries":"UBL.NS",     "United Spirits": "MCDOWELL-N.NS",
        "Whirlpool":     "WHIRLPOOL.NS", "Zydus Life":    "ZYDUSLIFE.NS",
    }

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────
def fetch_data(name: str, ticker: str, retries: int = 2) -> pd.DataFrame | None:
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

    for attempt in range(retries):
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
            if attempt < retries - 1:
                time.sleep(1)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS + MTF RSI
# ─────────────────────────────────────────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def build_mtf(df: pd.DataFrame) -> pd.DataFrame | None:
    try:
        c = df["close"]
        df["rsi_daily"]  = calc_rsi(c, RSI_PERIOD)
        df["sma50"]      = c.rolling(50).mean()
        df["sma200"]     = c.rolling(200).mean()
        df["ema50"]      = c.ewm(span=50, adjust=False).mean()
        df["vol_ma"]     = df["volume"].rolling(20).mean()

        h, l = df["high"], df["low"]
        tr   = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        up   = h.diff(); dn = -l.diff()
        pdm  = np.where((up>dn)&(up>0), up, 0.0)
        mdm  = np.where((dn>up)&(dn>0), dn, 0.0)
        atr_ = tr.ewm(span=14, adjust=False).mean()
        pdi  = 100*pd.Series(pdm,index=df.index).ewm(span=14,adjust=False).mean()/atr_
        mdi  = 100*pd.Series(mdm,index=df.index).ewm(span=14,adjust=False).mean()/atr_
        dx   = 100*(pdi-mdi).abs()/(pdi+mdi+1e-9)
        df["adx"] = dx.ewm(span=14, adjust=False).mean()

        wc = df["close"].resample("W-FRI").last().dropna()
        wr = calc_rsi(wc, RSI_PERIOD)
        df["rsi_weekly"] = wr.reindex(df.index, method="ffill")

        mc = df["close"].resample("ME").last().dropna()
        mr = calc_rsi(mc, RSI_PERIOD)
        df["rsi_monthly"] = mr.reindex(df.index, method="ffill")

        df = df.dropna(subset=["rsi_daily","rsi_weekly","rsi_monthly","sma200","ema50","adx"])
        return df if len(df) >= WARMUP + 10 else None
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# REGIME
# ─────────────────────────────────────────────────────────────────────────────
def get_regime(row: pd.Series) -> str:
    if row["close"] > row["sma200"] and row["sma50"] > row["sma200"]:
        return "bull"
    if row["close"] < row["sma200"] and row["sma50"] < row["sma200"]:
        return "bear"
    return "sideways"

# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE  (exact Vishal Malkan rules from transcript)
# ─────────────────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, strategy_name: str) -> tuple[list, list]:
    trades, equity   = [], []
    cash             = float(INITIAL_CAPITAL)
    shares           = 0
    entry_p          = 0.0
    entry_d          = None
    entry_idx        = 0
    entry_high       = 0.0
    entry_candle_low = 0.0
    entry_reg        = "sideways"
    rsi_reset        = True

    for i in range(WARMUP, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i-1]
        cur_d = row.name.date() if hasattr(row.name,"date") else \
                date.fromisoformat(str(row.name)[:10])

        price      = float(row["close"])
        rsi_d      = float(row["rsi_daily"])
        rsi_w      = float(row["rsi_weekly"])
        rsi_m      = float(row["rsi_monthly"])
        rsi_d_prev = float(prev["rsi_daily"])
        vol        = float(row["volume"])
        vol_ma     = float(row["vol_ma"]) if not pd.isna(row["vol_ma"]) else vol
        adx        = float(row["adx"])
        ema50      = float(row["ema50"])
        sma200     = float(row["sma200"])
        regime     = get_regime(row)

        port_val = cash + shares * price
        if i % 5 == 0:
            equity.append({"date": cur_d, "value": port_val, "regime": regime})

        # ── EXIT ─────────────────────────────────────────────────────────────
        if shares > 0:
            exit_reason = None

            # Trailing stop: 4-bar low after RSI has been above 60
            max_rsi_since_entry = df.iloc[entry_idx:i+1]["rsi_daily"].max()
            if max_rsi_since_entry >= 60:
                trail_low = float(df.iloc[max(0,i-4):i]["low"].min())
                if price < trail_low:
                    exit_reason = "Trailing Stop (4-bar)"

            # Primary: RSI crosses 60 (Malkan exact)
            if not exit_reason and rsi_d >= 60 and rsi_d_prev < 60:
                exit_reason = "RSI Target 60"

            # Hard stop: below signal candle low
            if not exit_reason and price < entry_candle_low:
                exit_reason = "Stop (candle low)"

            # Weekly RSI breaks 50 — father trend broken
            if not exit_reason and rsi_w < 50 and float(prev["rsi_weekly"]) >= 50:
                exit_reason = "Weekly RSI < 50"

            # Advanced: price < 50 EMA
            if not exit_reason and strategy_name == "GFS Advanced" and price < ema50:
                exit_reason = "Price < 50 EMA"

            # Max hold 180 days
            if not exit_reason and (i - entry_idx) >= 180:
                exit_reason = "Max Hold 180d"

            if exit_reason:
                ep      = price * (1 - TC["slippage_pct"])
                gross   = shares * (ep - entry_p)
                costs   = calc_costs(shares * entry_p, shares * ep)
                net     = gross - costs
                pnl_pct = net / (shares * entry_p) * 100

                t_reg   = df.iloc[entry_idx:i+1].apply(get_regime, axis=1)
                dom_reg = t_reg.value_counts().idxmax() if len(t_reg) else "unknown"

                trades.append({
                    "strategy":    strategy_name,
                    "instrument":  "",
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
                })
                cash             += shares * ep - costs
                shares            = 0
                entry_p           = 0.0
                entry_high        = 0.0
                entry_candle_low  = 0.0
                rsi_reset         = False

        # ── ENTRY ─────────────────────────────────────────────────────────────
        if shares == 0:
            if not rsi_reset and rsi_d > 55:
                rsi_reset = True

            # GFS Basic — exact transcript rules
            gfs_basic = (
                rsi_m > 60 and
                rsi_w > 60 and
                rsi_d >= 35 and
                rsi_d < 45 and
                float(row["close"]) > float(row["open"])  # green alert candle
            )

            # GFS Advanced — adds structural quality filters
            gfs_advanced = gfs_basic and (
                price > ema50 and
                price > sma200 and
                adx > 20 and
                vol < vol_ma * 1.0   # low/normal vol on pullback = healthy
            )

            condition = (gfs_advanced if strategy_name == "GFS Advanced"
                         else gfs_basic)

            if condition and rsi_reset:
                # Entry above HIGH of signal candle (Malkan exact)
                exec_price = float(row["high"]) * (1 + TC["slippage_pct"])
                buyable    = int(cash * POSITION_SIZE / exec_price)
                if buyable > 0 and cash > exec_price * buyable:
                    shares           = buyable
                    entry_p          = exec_price
                    entry_d          = cur_d
                    entry_idx        = i
                    entry_high       = float(row["high"])
                    entry_candle_low = float(row["low"]) * (1 - TC["slippage_pct"])
                    entry_reg        = regime
                    cash            -= shares * exec_price

    return trades, equity

# ─────────────────────────────────────────────────────────────────────────────
# LIVE SCREENER — today's GFS status for every stock
# ─────────────────────────────────────────────────────────────────────────────
def run_live_screener(instruments: dict) -> pd.DataFrame:
    """
    For each stock, show today's RSI values and GFS signal status.
    Useful for knowing which stocks are currently in the GFS zone.
    """
    rows = []
    for name, ticker in instruments.items():
        df_raw = fetch_data(name, ticker)
        if df_raw is None:
            continue
        df = build_mtf(df_raw)
        if df is None or len(df) < 5:
            continue

        cur   = df.iloc[-1]
        price = float(cur["close"])
        rsi_d = float(cur["rsi_daily"])
        rsi_w = float(cur["rsi_weekly"])
        rsi_m = float(cur["rsi_monthly"])
        ema50 = float(cur["ema50"])
        sma200= float(cur["sma200"])
        adx   = float(cur["adx"])
        green = float(cur["close"]) > float(cur["open"])

        gfs_grandfather = rsi_m > 60
        gfs_father      = rsi_w > 60
        gfs_son         = 35 <= rsi_d < 45

        # Signal quality
        if gfs_grandfather and gfs_father and gfs_son and green:
            signal = "🟢 ACTIVE SIGNAL"
        elif gfs_grandfather and gfs_father and 35 <= rsi_d < 50:
            signal = "⚡ APPROACHING"
        elif gfs_grandfather and gfs_father:
            signal = "👀 WATCHING (M+W ok)"
        elif gfs_grandfather:
            signal = "📊 MONTHLY OK"
        else:
            signal = "⏳ NOT READY"

        rows.append({
            "Name":       name,
            "Ticker":     ticker,
            "Price":      round(price, 1),
            "RSI Monthly":round(rsi_m, 1),
            "RSI Weekly": round(rsi_w, 1),
            "RSI Daily":  round(rsi_d, 1),
            "Above EMA50":price > ema50,
            "Above SMA200":price > sma200,
            "ADX":        round(adx, 1),
            "GFS Signal": signal,
        })

    df_out = pd.DataFrame(rows)
    if not df_out.empty:
        df_out = df_out.sort_values("GFS Signal")
    return df_out

# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────
def calc_metrics(trades: list, equity: list, strategy: str,
                 instrument: str, min_trades: int = 3) -> dict | None:
    if len(trades) < min_trades or not equity:
        return None

    eq_vals = [e["value"] for e in equity]
    init    = float(INITIAL_CAPITAL)
    final   = float(eq_vals[-1])
    years   = max((equity[-1]["date"] - equity[0]["date"]).days / 365.25, 0.5)

    if final <= 0:
        return None

    cagr      = ((final / init) ** (1 / years) - 1) * 100
    total_ret = (final - init) / init * 100

    peak = init; max_dd = 0.0
    for v in eq_vals:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    rets   = [(eq_vals[i]-eq_vals[i-1])/eq_vals[i-1] for i in range(1,len(eq_vals))]
    mr, sr = np.mean(rets), np.std(rets)
    sharpe = (mr/sr*np.sqrt(252)) if sr > 1e-9 else 0.0
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

    rs = {}
    for reg in ["bull","bear","sideways"]:
        rt = [t for t in trades if t["regime"] == reg]
        rw = [t for t in rt if t["net_pnl"] > 0]
        rs[reg] = {
            "trades":    len(rt),
            "win_rate":  round(len(rw)/len(rt)*100, 1) if rt else 0.0,
            "avg_pnl":   round(float(np.mean([t["pnl_pct"] for t in rt])), 2) if rt else 0.0,
        }

    rsi_entries = [t["rsi_entry_d"] for t in trades if "rsi_entry_d" in t]
    avg_rsi = round(np.mean(rsi_entries), 1) if rsi_entries else 0.0

    annual = {}
    for t in trades:
        yr = str(t["exit_date"])[:4]
        annual[yr] = round(annual.get(yr, 0.0) + t["net_pnl"], 0)

    return {
        "strategy":      strategy,
        "instrument":    instrument,
        "cagr":          round(cagr, 2),
        "total_return":  round(total_ret, 2),
        "max_dd":        round(max_dd, 2),
        "sharpe":        round(sharpe, 2),
        "calmar":        round(calmar, 2),
        "win_rate":      round(wr, 1),
        "total_trades":  len(trades),
        "avg_win":       round(aw, 2),
        "avg_loss":      round(al, 2),
        "profit_factor": round(min(pf, 99.0), 2),
        "avg_duration":  round(dur, 1),
        "total_costs":   round(costs, 0),
        "expectancy":    round(exp, 2),
        "avg_rsi_entry": avg_rsi,
        "years":         round(years, 1),
        "regime_stats":  rs,
        "annual_returns":annual,
    }

# ─────────────────────────────────────────────────────────────────────────────
# PRINT RESULTS
# ─────────────────────────────────────────────────────────────────────────────
def _rc(val, good, ok, fmt=".1f"):
    if not RICH: return str(round(val, 2))
    if val >= good: return f"[bold green]{val:{fmt}}[/]"
    if val >= ok:   return f"[yellow]{val:{fmt}}[/]"
    return f"[red]{val:{fmt}}[/]"

def print_results(all_metrics: list, top_n: int = 30):
    ranked = sorted(all_metrics, key=lambda x: -x["sharpe"])
    if top_n: ranked = ranked[:top_n]

    if not RICH:
        print(f"\n{'─'*140}")
        print(f"{'#':>3}  {'Strategy':<14} {'Instrument':<22} "
              f"{'CAGR%':>6} {'Sharpe':>7} {'MaxDD%':>7} {'WR%':>6} "
              f"{'PF':>5} {'Trades':>7} {'AvgRSI':>7} {'BullWR':>7}")
        print(f"{'─'*140}")
        for i, m in enumerate(ranked, 1):
            rs = m["regime_stats"]
            print(f"{i:>3}  {m['strategy']:<14} {m['instrument']:<22} "
                  f"{m['cagr']:>6.1f} {m['sharpe']:>7.2f} {m['max_dd']:>7.1f} "
                  f"{m['win_rate']:>6.1f} {m['profit_factor']:>5.2f} "
                  f"{m['total_trades']:>7} {m['avg_rsi_entry']:>7.1f} "
                  f"{rs['bull']['win_rate']:>6}%")
        print(f"{'─'*140}")
        return

    table = Table(
        title=f"[bold cyan]◈ GFS Nifty 500 — Top {len(ranked)} by Sharpe[/]",
        header_style="bold blue", border_style="dim cyan", show_lines=False,
    )
    for col, j in [
        ("#","right"),("Type","left"),("Instrument","left"),
        ("CAGR%","right"),("Sharpe","right"),("MaxDD%","right"),
        ("Win%","right"),("PF","right"),("Calmar","right"),
        ("Trades","right"),("AvgDur","right"),("RSI@Entry","right"),
        ("Bull WR","right"),("Bear WR","right"),
    ]:
        table.add_column(col, justify=j, no_wrap=True)

    for i, m in enumerate(ranked, 1):
        rs    = m["regime_stats"]
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else str(i)
        is_adv = "Advanced" in m["strategy"]
        table.add_row(
            medal,
            f"{'🔵 Adv' if is_adv else '⚪ Bas'}",
            m["instrument"],
            _rc(m["cagr"],       15,   8, ".1f"),
            _rc(m["sharpe"],    1.5, 0.8, ".2f"),
            f"[{'red' if m['max_dd']>25 else 'yellow' if m['max_dd']>12 else 'green'}]{m['max_dd']:.1f}[/]",
            _rc(m["win_rate"],   58,  48, ".1f"),
            _rc(m["profit_factor"],1.8,1.2,".2f"),
            _rc(m["calmar"],    0.8, 0.4, ".2f"),
            str(m["total_trades"]),
            f"{m['avg_duration']:.0f}d",
            f"[cyan]{m['avg_rsi_entry']}[/]",
            f"[green]{rs['bull']['win_rate']}%[/]",
            f"[{'red' if rs['bear']['win_rate']<40 else 'yellow'}]{rs['bear']['win_rate']}%[/]",
        )
    console.print(table)

def save_results(all_metrics: list, all_trades: list,
                 screener_df: pd.DataFrame | None = None) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if all_metrics:
        rows = []
        for m in sorted(all_metrics, key=lambda x: -x["sharpe"]):
            row = {k: v for k, v in m.items()
                   if k not in ("regime_stats","annual_returns")}
            for reg in ["bull","bear","sideways"]:
                row[f"{reg}_wr"]  = m["regime_stats"][reg]["win_rate"]
                row[f"{reg}_pnl"] = m["regime_stats"][reg]["avg_pnl"]
            rows.append(row)
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"gfs500_summary_{ts}.csv", index=False)

    if all_trades:
        pd.DataFrame(all_trades).to_csv(
            RESULTS_DIR / f"gfs500_trades_{ts}.csv", index=False)

    if screener_df is not None and not screener_df.empty:
        screener_df.to_csv(RESULTS_DIR / f"gfs500_screener_{ts}.csv", index=False)
        print(f"\n📋 Live screener saved → results/gfs500_screener_{ts}.csv")

    return ts

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
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
    parser = argparse.ArgumentParser(description="GFS Nifty 500 Backtester")
    parser.add_argument("--top",          type=int, default=30,
                        help="Show top N results (default 30)")
    parser.add_argument("--min-trades",   type=int, default=3,
                        help="Minimum trades to include in results (default 3)")
    parser.add_argument("--advanced-only",action="store_true")
    parser.add_argument("--screener-only",action="store_true",
                        help="Only run live screener, skip backtest")
    parser.add_argument("--no-telegram",  action="store_true")
    parser.add_argument("--clear-cache",  action="store_true")
    args = parser.parse_args()

    if args.clear_cache:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        if (CACHE_DIR / "nifty500_list.csv").exists():
            (CACHE_DIR / "nifty500_list.csv").unlink()
        print("Cache cleared.")

    # ── Fetch instrument list ──────────────────────────────────────────────────
    instruments = fetch_nifty500_list()
    n = len(instruments)

    strategies = (["GFS Advanced"] if args.advanced_only
                  else ["GFS Basic", "GFS Advanced"])

    if RICH:
        console.print(Panel(
            f"[bold cyan]◈  GFS NIFTY 500 BACKTESTER[/]\n"
            f"Universe: [bold]{n} stocks[/]  ·  "
            f"Strategies: [bold]{len(strategies)}[/]  ·  "
            f"Period: [bold]{START_DATE} → {END_DATE}[/]\n"
            f"Rules: Monthly RSI>60 · Weekly RSI>60 · Daily RSI 35-45 · Green candle · Entry above high\n"
            f"Stop: Below signal candle low  ·  Exit: RSI crosses 60 + 4-bar trailing stop",
            border_style="blue", padding=(0,2)
        ))
    else:
        print(f"\n◈  GFS Nifty 500 | {n} stocks × {len(strategies)} strategies")

    # ── Live screener (always run) ─────────────────────────────────────────────
    print("\n📋 Running live GFS screener...")
    screener_df = run_live_screener(instruments)
    active = screener_df[screener_df["GFS Signal"].str.startswith("🟢")]
    approaching = screener_df[screener_df["GFS Signal"].str.startswith("⚡")]

    if RICH:
        if not active.empty:
            console.print(Panel(
                "[bold green]🟢 ACTIVE GFS SIGNALS TODAY:[/]\n" +
                "\n".join(f"  {r['Name']:<20} RSI_M:{r['RSI Monthly']} "
                          f"RSI_W:{r['RSI Weekly']} RSI_D:{r['RSI Daily']}"
                          for _, r in active.iterrows()),
                border_style="green", padding=(0,2)
            ))
        if not approaching.empty:
            console.print(Panel(
                "[bold yellow]⚡ APPROACHING SIGNAL:[/]\n" +
                "\n".join(f"  {r['Name']:<20} RSI_D:{r['RSI Daily']}"
                          for _, r in approaching.iterrows()),
                border_style="yellow", padding=(0,2)
            ))
        if active.empty and approaching.empty:
            console.print("[dim]No active or approaching GFS signals today.[/]")

    if args.screener_only:
        save_results([], [], screener_df)
        return

    # ── Full backtest ──────────────────────────────────────────────────────────
    total_runs  = n * len(strategies)
    all_metrics = []
    all_trades  = []
    done        = 0
    skipped     = 0

    if RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}[/]"),
            TimeRemainingColumn(),
            console=console,
        )
        task = progress.add_task("Running GFS 500...", total=total_runs)
        progress.start()

    for inst_name, ticker in instruments.items():
        if RICH:
            progress.update(task, description=f"↓ {inst_name:<22}")

        df_raw = fetch_data(inst_name, ticker)
        if df_raw is None:
            done += len(strategies)
            skipped += len(strategies)
            if RICH: progress.update(task, advance=len(strategies))
            continue

        df = build_mtf(df_raw.copy())
        if df is None:
            done += len(strategies)
            skipped += len(strategies)
            if RICH: progress.update(task, advance=len(strategies))
            continue

        for strat in strategies:
            done += 1
            if RICH:
                progress.update(task,
                    description=f"  {strat:<14} × {inst_name:<20}",
                    advance=1)
            else:
                print(f"  [{done/total_runs*100:5.1f}%] {strat} × {inst_name}",
                      end="\r")

            try:
                trades, equity = run_backtest(df, strat)
                m = calc_metrics(trades, equity, strat, inst_name,
                                 min_trades=args.min_trades)
                if m:
                    all_metrics.append(m)
                    for t in trades:
                        t["instrument"] = inst_name
                        all_trades.append(t)
            except Exception:
                pass

    if RICH: progress.stop()
    else:    print(" " * 80, end="\r")

    if not all_metrics:
        print("No valid results.")
        return

    print_results(all_metrics, top_n=args.top)

    # Summary stats
    basic_m = [m for m in all_metrics if "Basic" in m["strategy"]]
    adv_m   = [m for m in all_metrics if "Advanced" in m["strategy"]]
    pos_b   = sum(1 for m in basic_m   if m["cagr"] > 0)
    pos_a   = sum(1 for m in adv_m     if m["cagr"] > 0)

    ts = save_results(all_metrics, all_trades, screener_df)

    best = sorted(all_metrics, key=lambda x: -x["sharpe"])[0]

    if RICH:
        def avg(lst, k):
            return round(np.mean([m[k] for m in lst]), 2) if lst else 0

        console.print(Panel(
            f"[bold]GFS Basic[/]    ({len(basic_m)} combos, {pos_b} positive):  "
            f"Avg CAGR [green]{avg(basic_m,'cagr')}%[/]  ·  "
            f"Avg Sharpe [cyan]{avg(basic_m,'sharpe')}[/]  ·  "
            f"Avg DD {avg(basic_m,'max_dd')}%\n"
            f"[bold]GFS Advanced[/] ({len(adv_m)} combos, {pos_a} positive):  "
            f"Avg CAGR [green]{avg(adv_m,'cagr')}%[/]  ·  "
            f"Avg Sharpe [cyan]{avg(adv_m,'sharpe')}[/]  ·  "
            f"Avg DD {avg(adv_m,'max_dd')}%\n\n"
            f"[bold]🏆 Best:[/] [cyan]{best['strategy']}[/] × [bold]{best['instrument']}[/]\n"
            f"CAGR [green]{best['cagr']}%[/]  ·  "
            f"Sharpe [cyan]{best['sharpe']}[/]  ·  "
            f"MaxDD [red]{best['max_dd']}%[/]  ·  "
            f"WR {best['win_rate']}%  ·  Trades {best['total_trades']}\n"
            f"Skipped: {skipped} (insufficient data)",
            title="[bold green]◈  GFS NIFTY 500 COMPLETE[/]", border_style="green"
        ))
    else:
        print(f"\nBest: {best['strategy']} × {best['instrument']} "
              f"| CAGR {best['cagr']}% | Sharpe {best['sharpe']}")
        print(f"Saved: results/gfs500_summary_{ts}.csv")

    if not args.no_telegram and all_metrics:
        top3 = sorted(all_metrics, key=lambda x: -x["sharpe"])[:3]
        active_list = "\n".join(
            f"  • {r['Name']} (RSI_D:{r['RSI Daily']})"
            for _, r in active.iterrows()
        ) if not active.empty else "  None today"

        msg = (
            f"📊 *GFS Nifty 500 Backtest*\n"
            f"_{START_DATE} → {END_DATE} · {n} stocks_\n\n"
            f"*🟢 Active signals today:*\n{active_list}\n\n"
            f"*Top 3 historical performers:*\n"
        )
        for i, m in enumerate(top3, 1):
            msg += (f"*{i}. {m['strategy'][:6]} × {m['instrument']}*\n"
                    f"CAGR `{m['cagr']}%` · Sharpe `{m['sharpe']}` · "
                    f"WR `{m['win_rate']}%` · Trades `{m['total_trades']}`\n\n")
        send_telegram(msg)

if __name__ == "__main__":
    main()
