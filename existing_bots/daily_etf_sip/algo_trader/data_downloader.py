#!/usr/bin/env python3
"""
Algo Trader — Data Downloader
Downloads historical OHLCV candles and saves as CSV.

Sources:
  - Binance public API  → BTC/USDT (no auth needed)
  - Kite Connect API    → MCX Gold Mini, Crude Oil Mini, Silver Mini

Usage:
  python data_downloader.py --instrument btc
  python data_downloader.py --instrument gold
  python data_downloader.py --instrument crude
  python data_downloader.py --all
"""

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR  = Path(__file__).resolve().parent
DATA_DIR  = BASE_DIR / "data"
TOKEN_FILE = Path("/home/ubuntu/fire_shop/.kite_token")   # reuse FIRE Shop token

DATA_DIR.mkdir(exist_ok=True)

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")

# ── Instrument config ─────────────────────────────────────────────────────────
# Each entry: symbol used in filename, fetch function key, display name
INSTRUMENTS = {
    "btc":   {"label": "BTC/USDT Perpetual",  "source": "binance", "symbol": "BTCUSDT",        "interval": "1h"},
    "gold":  {"label": "Gold Mini MCX",        "source": "kite",    "symbol": "GOLD24AUGFUT",   "interval": "60minute"},
    "crude": {"label": "Crude Oil Mini MCX",   "source": "kite",    "symbol": "CRUDEOIL24AUGFUT","interval": "60minute"},
    "silver":{"label": "Silver Mini MCX",      "source": "kite",    "symbol": "SILVER24AUGFUT", "interval": "60minute"},
}

# ── Binance ───────────────────────────────────────────────────────────────────

def fetch_binance_ohlcv(symbol="BTCUSDT", interval="1h", days=2000):
    """
    Fetch historical OHLCV from Binance public API.
    Returns list of dicts: [{"ts", "open", "high", "low", "close", "volume"}, ...]
    No API key needed.
    """
    print(f"  📥 Binance: {symbol} {interval} — last {days} days...")

    url     = "https://api.binance.com/api/v3/klines"
    end_ms  = int(datetime.utcnow().timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000

    all_candles = []
    limit       = 1000   # Binance max per request

    while start_ms < end_ms:
        params = {
            "symbol":    symbol,
            "interval":  interval,
            "startTime": start_ms,
            "endTime":   end_ms,
            "limit":     limit,
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f"    ❌ Fetch error: {e}")
            break

        if not batch:
            break

        for row in batch:
            all_candles.append({
                "ts":     datetime.utcfromtimestamp(row[0] / 1000).strftime("%Y-%m-%d %H:%M"),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })

        # Next batch starts after last candle
        start_ms = batch[-1][0] + 1
        print(f"    ... fetched up to {all_candles[-1]['ts']} ({len(all_candles)} candles)")
        time.sleep(0.3)

    print(f"  ✅ Total: {len(all_candles)} candles")
    return all_candles


# ── Kite (MCX) ────────────────────────────────────────────────────────────────

def get_kite_token():
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(f"Kite token file not found: {TOKEN_FILE}")
    data = json.loads(TOKEN_FILE.read_text())
    return data["access_token"]


def fetch_kite_ohlcv(tradingsymbol, exchange="MCX", interval="60minute", days=2000):
    """
    Fetch historical OHLCV from Kite Connect REST API.
    Kite allows max 60-day window per request for intraday — loop in 60-day chunks.
    """
    print(f"  📥 Kite: {exchange}:{tradingsymbol} {interval} — last {days} days...")

    access_token = get_kite_token()
    headers = {
        "X-Kite-Version": "3",
        "Authorization":  f"token {KITE_API_KEY}:{access_token}",
    }

    # First get instrument token
    try:
        r = requests.get(
            "https://api.kite.trade/instruments",
            headers=headers, timeout=15
        )
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        # CSV: instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,...,exchange
        instrument_token = None
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 9:
                continue
            if parts[2] == tradingsymbol and parts[-1].strip() == exchange:
                instrument_token = parts[0]
                break

        if not instrument_token:
            # Try front month — symbol might differ; grab first MCX match prefix
            prefix = tradingsymbol[:len(tradingsymbol)-8]   # e.g. "GOLD" from "GOLD24AUGFUT"
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) < 9:
                    continue
                if parts[2].startswith(prefix) and parts[-1].strip() == exchange and "FUT" in parts[2]:
                    instrument_token = parts[0]
                    tradingsymbol    = parts[2]
                    print(f"    ℹ️  Using active contract: {tradingsymbol}")
                    break

        if not instrument_token:
            print(f"    ❌ Instrument not found: {tradingsymbol}")
            return []

        print(f"    ✅ Instrument token: {instrument_token} ({tradingsymbol})")

    except Exception as e:
        print(f"    ❌ Instrument lookup failed: {e}")
        return []

    # Fetch in 60-day chunks
    all_candles = []
    end_dt      = datetime.now()
    start_dt    = end_dt - timedelta(days=days)

    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=60), end_dt)
        fmt = "%Y-%m-%d"
        url = (f"https://api.kite.trade/instruments/historical/{instrument_token}/{interval}"
               f"?from={chunk_start.strftime(fmt)}&to={chunk_end.strftime(fmt)}&continuous=1")
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            candles = data.get("data", {}).get("candles", [])
            for c in candles:
                all_candles.append({
                    "ts":     c[0][:16].replace("T", " "),
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                })
            print(f"    ... {chunk_start.strftime(fmt)} → {chunk_end.strftime(fmt)}: {len(candles)} candles")
        except Exception as e:
            print(f"    ⚠️  Chunk failed {chunk_start.strftime(fmt)}: {e}")

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.4)

    # Deduplicate by timestamp
    seen = set()
    deduped = []
    for c in all_candles:
        if c["ts"] not in seen:
            seen.add(c["ts"])
            deduped.append(c)
    deduped.sort(key=lambda x: x["ts"])

    print(f"  ✅ Total: {len(deduped)} candles")
    return deduped


# ── Save CSV ──────────────────────────────────────────────────────────────────

def save_csv(candles, filename):
    out_path = DATA_DIR / filename
    with open(out_path, "w") as f:
        f.write("ts,open,high,low,close,volume\n")
        for c in candles:
            f.write(f"{c['ts']},{c['open']},{c['high']},{c['low']},{c['close']},{c['volume']}\n")
    print(f"  💾 Saved: {out_path} ({len(candles)} rows)")
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def download(instrument_key):
    cfg = INSTRUMENTS.get(instrument_key)
    if not cfg:
        print(f"❌ Unknown instrument: {instrument_key}")
        return

    print(f"\n{'='*55}")
    print(f"  Downloading: {cfg['label']}")
    print(f"{'='*55}")

    if cfg["source"] == "binance":
        candles = fetch_binance_ohlcv(cfg["symbol"], cfg["interval"], days=2000)
    else:
        candles = fetch_kite_ohlcv(cfg["symbol"], exchange="MCX", interval=cfg["interval"], days=2000)

    if candles:
        fname = f"{instrument_key}_1h.csv"
        save_csv(candles, fname)
    else:
        print(f"  ⚠️  No data returned for {instrument_key}")


def main():
    parser = argparse.ArgumentParser(description="Algo Trader — Data Downloader")
    parser.add_argument("--instrument", choices=list(INSTRUMENTS.keys()),
                        help="Single instrument to download")
    parser.add_argument("--all", action="store_true", help="Download all instruments")
    args = parser.parse_args()

    if args.all:
        for key in INSTRUMENTS:
            download(key)
    elif args.instrument:
        download(args.instrument)
    else:
        parser.print_help()

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
