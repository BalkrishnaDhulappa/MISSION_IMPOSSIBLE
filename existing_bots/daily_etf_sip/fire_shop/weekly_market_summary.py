#!/usr/bin/env python3
"""
weekly_market_summary.py — Sunday Evening Market Intelligence Briefing
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sends a weekly Telegram message every Sunday evening covering:
  1. World market indices — weekly performance
  2. Indian market indices — weekly + monthly + YTD
  3. NSE sectoral indices — top 3 gainers + top 3 losers
  4. Top stocks inside best performing sector this week

Cron (Sunday 6:00 PM IST = 12:30 PM UTC):
  30 12 * * 0 /home/ubuntu/fire_shop/venv/bin/python3 \
    /home/ubuntu/fire_shop/weekly_market_summary.py \
    >> /home/ubuntu/fire_shop/logs/weekly_summary.log 2>&1

Usage:
  python weekly_market_summary.py          # run and send Telegram
  python weekly_market_summary.py --test   # print only, no Telegram
"""

import argparse
import os
import warnings
from datetime import datetime, date, timedelta

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

# ─────────────────────────────────────────────────────────────────────────────
# WORLD INDICES
# ─────────────────────────────────────────────────────────────────────────────
WORLD_INDICES = {
    "🇺🇸 S&P 500":     "^GSPC",
    "🇺🇸 Nasdaq":      "^IXIC",
    "🇺🇸 Dow Jones":   "^DJI",
    "🇬🇧 FTSE 100":    "^FTSE",
    "🇩🇪 DAX":         "^GDAXI",
    "🇯🇵 Nikkei":      "^N225",
    "🇭🇰 Hang Seng":   "^HSI",
    "🇨🇳 Shanghai":    "000001.SS",
    "🌏 MSCI EM":       "EEM",
}

# ─────────────────────────────────────────────────────────────────────────────
# INDIAN INDICES
# ─────────────────────────────────────────────────────────────────────────────
INDIA_INDICES = {
    "Nifty 50":       "^NSEI",
    "Sensex":         "^BSESN",
    "Nifty Bank":     "^NSEBANK",
    "Nifty Midcap":   "^CNXMIDCAP",
    "Nifty Smallcap": "^CNXSC",
    "India VIX":      "^INDIAVIX",
}

# ─────────────────────────────────────────────────────────────────────────────
# NSE SECTORAL INDICES
# ─────────────────────────────────────────────────────────────────────────────
SECTORAL_INDICES = {
    "IT":           "^CNXIT",
    "Bank":         "^NSEBANK",
    "Auto":         "^CNXAUTO",
    "Pharma":       "^CNXPHARMA",
    "FMCG":         "^CNXFMCG",
    "Metal":        "^CNXMETAL",
    "Realty":       "^CNXREALTY",
    "Energy":       "^CNXENERGY",
    "Infra":        "^CNXINFRA",
    "Media":        "^CNXMEDIA",
    "PSU Bank":     "^CNXPSUBANK",
    "Fin Services": "^CNXFIN",
    "Consumer Dur": "^CNXCONSUM",
    "Healthcare":   "^CNXHEALTH",
    "Oil & Gas":    "^CNXOILGAS",
}

# ─────────────────────────────────────────────────────────────────────────────
# STOCKS PER SECTOR (top liquid names)
# ─────────────────────────────────────────────────────────────────────────────
SECTOR_STOCKS = {
    "IT": {
        "TCS":"TCS.NS","Infosys":"INFY.NS","HCL Tech":"HCLTECH.NS",
        "Wipro":"WIPRO.NS","Tech Mah":"TECHM.NS","LTIMindtree":"LTIM.NS",
        "Mphasis":"MPHASIS.NS","Persistent":"PERSISTENT.NS",
    },
    "Bank": {
        "HDFC Bank":"HDFCBANK.NS","ICICI Bank":"ICICIBANK.NS","SBI":"SBIN.NS",
        "Kotak Bank":"KOTAKBANK.NS","Axis Bank":"AXISBANK.NS",
        "IndusInd":"INDUSINDBK.NS","AU Small Fin":"AUBANK.NS",
        "Federal Bank":"FEDERALBNK.NS",
    },
    "Auto": {
        "Maruti":"MARUTI.NS","Tata Motors":"TATAMOTORS.NS","M&M":"M&M.NS",
        "Bajaj Auto":"BAJAJ-AUTO.NS","Hero Moto":"HEROMOTOCO.NS",
        "Eicher":"EICHERMOT.NS","TVS Motor":"TVSMOTOR.NS",
    },
    "Pharma": {
        "Sun Pharma":"SUNPHARMA.NS","Dr Reddy":"DRREDDY.NS","Cipla":"CIPLA.NS",
        "Divi's Lab":"DIVISLAB.NS","Lupin":"LUPIN.NS",
        "Torrent Ph":"TORNTPHARM.NS","Ajanta Ph":"AJANTAPHARM.NS",
    },
    "FMCG": {
        "HUL":"HINDUNILVR.NS","ITC":"ITC.NS","Nestle":"NESTLEIND.NS",
        "Britannia":"BRITANNIA.NS","Dabur":"DABUR.NS","Marico":"MARICO.NS",
        "Colgate":"COLPAL.NS","Varun Bev":"VBL.NS",
    },
    "Metal": {
        "Tata Steel":"TATASTEEL.NS","JSW Steel":"JSWSTEEL.NS","Hindalco":"HINDALCO.NS",
        "Vedanta":"VEDL.NS","SAIL":"SAIL.NS","Coal India":"COALINDIA.NS",
        "NMDC":"NMDC.NS","Jindal Steel":"JINDALSTEL.NS",
    },
    "Realty": {
        "DLF":"DLF.NS","Godrej Props":"GODREJPROP.NS","Prestige":"PRESTIGE.NS",
        "Oberoi":"OBEROIRLTY.NS","Macrotech":"LODHA.NS","Brigade":"BRIGADE.NS",
    },
    "Energy": {
        "ONGC":"ONGC.NS","Reliance":"RELIANCE.NS","BPCL":"BPCL.NS",
        "IOC":"IOC.NS","NTPC":"NTPC.NS","Power Grid":"POWERGRID.NS",
        "Tata Power":"TATAPOWER.NS","Adani Grn":"ADANIGREEN.NS",
    },
    "Infra": {
        "L&T":"LT.NS","Adani Ports":"ADANIPORTS.NS","HAL":"HAL.NS",
        "BEL":"BEL.NS","ABB":"ABB.NS","Siemens":"SIEMENS.NS",
        "CG Power":"CGPOWER.NS","Bharat Forge":"BHARATFORG.NS",
    },
    "PSU Bank": {
        "SBI":"SBIN.NS","Bank of Baroda":"BANKBARODA.NS","Canara Bank":"CANBK.NS",
        "PNB":"PNB.NS","Union Bank":"UNIONBANK.NS","Indian Bank":"INDIANB.NS",
    },
    "Fin Services": {
        "Bajaj Fin":"BAJFINANCE.NS","Bajaj Finserv":"BAJAJFINSV.NS",
        "Cholamandalam":"CHOLAFIN.NS","Muthoot":"MUTHOOTFIN.NS",
        "HDFC AMC":"HDFCAMC.NS","CDSL":"CDSL.NS","360 ONE":"360ONE.NS",
    },
    "Media": {
        "Zee Ent":"ZEEL.NS","PVR Inox":"PVRINOX.NS","Sun TV":"SUNTV.NS",
        "TV18":"TV18BRDCST.NS",
    },
    "Consumer Dur": {
        "Havells":"HAVELLS.NS","Titan":"TITAN.NS","Voltas":"VOLTAS.NS",
        "Whirlpool":"WHIRLPOOL.NS","Blue Star":"BLUESTARCO.NS","Dixon":"DIXON.NS",
    },
    "Healthcare": {
        "Apollo Hosp":"APOLLOHOSP.NS","Fortis":"FORTIS.NS",
        "Max Health":"MAXHEALTH.NS","Aster DM":"ASTERDM.NS",
        "Metropolis":"METROPOLIS.NS",
    },
    "Oil & Gas": {
        "Reliance":"RELIANCE.NS","ONGC":"ONGC.NS","BPCL":"BPCL.NS",
        "IOC":"IOC.NS","HPCL":"HINDPETRO.NS","GAIL":"GAIL.NS",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────────────────────────────────────
def get_perf(ticker: str, days_back: int = 7) -> dict | None:
    """
    Fetch price data and compute weekly, monthly, YTD returns.
    Returns dict with performance metrics or None on failure.
    """
    try:
        raw = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
        if raw is None or len(raw) < 5:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        close = raw["Close"].squeeze().dropna()
        if len(close) < 5:
            return None

        price_now   = float(close.iloc[-1])
        # Weekly: 5 trading days back
        price_1w    = float(close.iloc[-min(6, len(close))])
        # Monthly: ~21 trading days
        price_1m    = float(close.iloc[-min(22, len(close))])
        # 3-month
        price_3m    = float(close.iloc[-min(65, len(close))])
        # YTD: from Jan 1 close
        year_start  = date.today().replace(month=1, day=1)
        ytd_df      = close[close.index >= pd.Timestamp(year_start)]
        price_ytd   = float(ytd_df.iloc[0]) if len(ytd_df) > 0 else price_1m

        def pct(old, new): return round((new - old) / old * 100, 2)

        return {
            "price":  round(price_now, 2),
            "1w":     pct(price_1w,  price_now),
            "1m":     pct(price_1m,  price_now),
            "3m":     pct(price_3m,  price_now),
            "ytd":    pct(price_ytd, price_now),
        }
    except Exception:
        return None

def arrow(val: float) -> str:
    """Return colored arrow for performance value."""
    if val > 0:   return "🟢"
    elif val < 0: return "🔴"
    else:         return "⚪"

def fmt_pct(val: float) -> str:
    return f"{'+' if val >= 0 else ''}{val:.1f}%"

# ─────────────────────────────────────────────────────────────────────────────
# SECTION BUILDERS
# ─────────────────────────────────────────────────────────────────────────────
def build_world_section() -> tuple[str, dict]:
    """Fetch and format world indices weekly performance."""
    print("  Fetching world indices...")
    results = {}
    for name, ticker in WORLD_INDICES.items():
        d = get_perf(ticker)
        if d:
            results[name] = d

    lines = ["*🌍 World Markets — Weekly*"]
    for name, d in results.items():
        lines.append(
            f"  {arrow(d['1w'])} {name}: `{fmt_pct(d['1w'])}` "
            f"| 1M `{fmt_pct(d['1m'])}`"
        )
    return "\n".join(lines), results

def build_india_section() -> tuple[str, dict]:
    """Fetch and format Indian indices — weekly + monthly + YTD."""
    print("  Fetching Indian indices...")
    results = {}
    for name, ticker in INDIA_INDICES.items():
        d = get_perf(ticker)
        if d:
            results[name] = d

    lines = ["*🇮🇳 Indian Markets*"]
    for name, d in results.items():
        if name == "India VIX":
            lines.append(f"  📊 {name}: `{d['price']:.1f}` | Chg `{fmt_pct(d['1w'])}`")
        else:
            lines.append(
                f"  {arrow(d['1w'])} {name}: "
                f"1W `{fmt_pct(d['1w'])}` | "
                f"1M `{fmt_pct(d['1m'])}` | "
                f"YTD `{fmt_pct(d['ytd'])}`"
            )
    return "\n".join(lines), results

def build_sectoral_section() -> tuple[str, str, list]:
    """
    Fetch sectoral indices, find top 3 gainers + top 3 losers.
    Returns (formatted_text, best_sector_name, sector_results_list)
    """
    print("  Fetching sectoral indices...")
    results = []
    for name, ticker in SECTORAL_INDICES.items():
        d = get_perf(ticker)
        if d:
            results.append({"name": name, **d})

    results.sort(key=lambda x: -x["1w"])
    gainers = results[:3]
    losers  = results[-3:][::-1]   # worst first

    lines = ["*📊 NSE Sectors — This Week*"]
    lines.append("_Top gainers:_")
    for s in gainers:
        lines.append(
            f"  🟢 *{s['name']}* `{fmt_pct(s['1w'])}` "
            f"| 1M `{fmt_pct(s['1m'])}`"
        )
    lines.append("_Top losers:_")
    for s in losers:
        lines.append(
            f"  🔴 *{s['name']}* `{fmt_pct(s['1w'])}` "
            f"| 1M `{fmt_pct(s['1m'])}`"
        )

    best_sector = gainers[0]["name"] if gainers else None
    return "\n".join(lines), best_sector, results

def build_sector_stocks_section(sector_name: str) -> str:
    """
    Fetch individual stocks inside the best-performing sector.
    Show top 5 by weekly return.
    """
    if sector_name not in SECTOR_STOCKS:
        return f"*📈 {sector_name} — Top stocks:*\n  _Stock data unavailable_"

    print(f"  Fetching {sector_name} stocks...")
    stocks = SECTOR_STOCKS[sector_name]
    results = []

    for name, ticker in stocks.items():
        d = get_perf(ticker)
        if d:
            results.append({"name": name, "ticker": ticker, **d})

    results.sort(key=lambda x: -x["1w"])

    lines = [f"*📈 Best sector: {sector_name} — Top stocks*"]
    for s in results[:5]:
        lines.append(
            f"  {arrow(s['1w'])} *{s['name']}* `₹{s['price']:.0f}` "
            f"| 1W `{fmt_pct(s['1w'])}` "
            f"| 1M `{fmt_pct(s['1m'])}`"
        )

    # Also show worst stocks in the best sector for context
    losers = sorted(results, key=lambda x: x["1w"])[:2]
    if losers:
        lines.append("_Laggards within sector:_")
        for s in losers:
            lines.append(
                f"  {arrow(s['1w'])} {s['name']} "
                f"`{fmt_pct(s['1w'])}`"
            )

    return "\n".join(lines)

def build_breadth_note(india_results: dict) -> str:
    """Quick market breadth context line."""
    nifty = india_results.get("Nifty 50", {})
    midcap = india_results.get("Nifty Midcap", {})
    smallcap = india_results.get("Nifty Smallcap", {})
    vix = india_results.get("India VIX", {})

    if not nifty:
        return ""

    # Breadth assessment
    if (nifty.get("1w", 0) > 0 and
        midcap.get("1w", 0) > nifty.get("1w", 0) and
        smallcap.get("1w", 0) > midcap.get("1w", 0)):
        breadth = "🟢 _Broad rally — small/midcaps leading_"
    elif (nifty.get("1w", 0) > 0 and
          midcap.get("1w", 0) < nifty.get("1w", 0)):
        breadth = "🟡 _Narrow rally — large caps only_"
    elif (nifty.get("1w", 0) < 0 and
          midcap.get("1w", 0) < nifty.get("1w", 0)):
        breadth = "🔴 _Broad selloff — small/midcaps hit harder_"
    elif nifty.get("1w", 0) < 0:
        breadth = "🟡 _Weak market — midcaps relatively resilient_"
    else:
        breadth = "⚪ _Mixed breadth_"

    vix_note = ""
    if vix:
        v = vix.get("price", 0)
        vix_note = f" | VIX `{v:.1f}` {'⚠ Elevated' if v > 20 else '✅ Normal'}"

    return f"{breadth}{vix_note}"

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(msg: str, test_mode: bool = False):
    if test_mode:
        print("\n" + "=" * 60)
        print("WEEKLY MARKET SUMMARY PREVIEW:")
        print("=" * 60)
        print(msg.replace("*","").replace("`","").replace("_",""))
        print("=" * 60)
        return

    # Split into chunks if too long
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    for chunk in chunks:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "text":       chunk,
                    "parse_mode": "Markdown",
                },
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
    parser.add_argument("--test",  action="store_true",
                        help="Print to terminal only, no Telegram")
    parser.add_argument("--force", action="store_true",
                        help="Run on any day (not just Sunday)")
    args = parser.parse_args()

    today = date.today()
    if today.weekday() != 6 and not args.force:
        print(f"Only runs on Sundays. Today is {today.strftime('%A')}. Use --force.")
        return

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Weekly market summary...")

    week_end   = today
    week_start = today - timedelta(days=6)

    # ── Fetch all data ─────────────────────────────────────────────────────────
    world_section,  world_data    = build_world_section()
    india_section,  india_data    = build_india_section()
    sector_section, best_sector, sector_data = build_sectoral_section()
    stocks_section = build_sector_stocks_section(best_sector) if best_sector else ""
    breadth_note   = build_breadth_note(india_data)

    # ── Build message ──────────────────────────────────────────────────────────
    nifty    = india_data.get("Nifty 50", {})
    nifty_1w = nifty.get("1w", 0)
    nifty_ytd = nifty.get("ytd", 0)

    header = (
        f"📅 *Weekly Market Wrap*\n"
        f"_{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}_\n"
        f"Nifty: `{fmt_pct(nifty_1w)}` this week | YTD `{fmt_pct(nifty_ytd)}`\n"
        f"{breadth_note}\n"
    )

    msg = "\n\n".join(filter(None, [
        header,
        world_section,
        india_section,
        sector_section,
        stocks_section,
        "_Next weekly wrap: Sunday 6 PM IST_",
    ]))

    print(f"  Message length: {len(msg)} chars")
    send_telegram(msg, args.test)
    print("  Done.")

if __name__ == "__main__":
    main()
