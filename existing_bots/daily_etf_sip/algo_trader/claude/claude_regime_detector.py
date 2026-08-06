#!/usr/bin/env python3
"""
Algo Trader — Regime Detector
Classifies market regime from OHLCV data.

Regimes:
  trending  — ADX > 25, clear directional move → EMA crossover strategy
  ranging   — ADX 15–25, price oscillating     → Grid bot strategy
  volatile  — ATR spike > 2x 20-period avg     → Pause / tighten stops

Output:
  {
    "regime":    "trending" | "ranging" | "volatile",
    "direction": "up" | "down" | "flat",   # only meaningful in trending
    "adx":       float,
    "atr":       float,
    "atr_ratio": float,   # current ATR / 20-period avg ATR
    "ema_fast":  float,
    "ema_slow":  float,
  }

Usage (standalone):
  python regime_detector.py --csv data/btc_1h.csv
  python regime_detector.py --csv data/gold_1h.csv --adx-trend 25 --adx-range 15
"""

import argparse
import csv
import json
from pathlib import Path


# ── Core indicator calculations ───────────────────────────────────────────────

def ema(values, period):
    """Exponential moving average. Returns list same length as input."""
    k = 2 / (period + 1)
    result = [None] * len(values)
    # Seed with SMA of first `period` values
    valid = [v for v in values[:period] if v is not None]
    if len(valid) < period:
        return result
    result[period - 1] = sum(valid) / period
    for i in range(period, len(values)):
        if values[i] is None or result[i - 1] is None:
            result[i] = result[i - 1]
        else:
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def compute_atr(highs, lows, closes, period=14):
    """Average True Range."""
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)

    atrs = [None] * (len(closes))
    if len(trs) < period:
        return atrs

    # Seed
    atrs[period] = sum(trs[:period]) / period
    for i in range(period + 1, len(closes)):
        atrs[i] = (atrs[i - 1] * (period - 1) + trs[i - 1]) / period

    return atrs


def compute_adx(highs, lows, closes, period=14):
    """
    ADX (Average Directional Index).
    Returns (adx_list, plus_di_list, minus_di_list)
    """
    n = len(closes)
    plus_dm  = [0.0] * n
    minus_dm = [0.0] * n
    trs      = [0.0] * n

    for i in range(1, n):
        up   = highs[i]  - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i]  = up   if up > down and up > 0   else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        trs[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )

    def smooth(arr, p):
        out = [0.0] * n
        if n <= p:
            return out
        out[p] = sum(arr[1:p + 1])
        for i in range(p + 1, n):
            out[i] = out[i - 1] - out[i - 1] / p + arr[i]
        return out

    s_tr      = smooth(trs, period)
    s_plus    = smooth(plus_dm, period)
    s_minus   = smooth(minus_dm, period)

    plus_di  = [100 * s_plus[i]  / s_tr[i] if s_tr[i] > 0 else 0.0 for i in range(n)]
    minus_di = [100 * s_minus[i] / s_tr[i] if s_tr[i] > 0 else 0.0 for i in range(n)]

    dx = [
        100 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i])
        if (plus_di[i] + minus_di[i]) > 0 else 0.0
        for i in range(n)
    ]

    adx = [0.0] * n
    if n <= 2 * period:
        return adx, plus_di, minus_di

    adx[2 * period] = sum(dx[period:2 * period + 1]) / (period + 1)
    for i in range(2 * period + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, plus_di, minus_di


# ── Regime classification ─────────────────────────────────────────────────────

def detect_regime(candles, adx_trend=25, adx_range=15,
                  atr_spike_ratio=2.0, ema_fast=20, ema_slow=50,
                  adx_period=14, atr_period=14):
    """
    Classify the current market regime from a list of OHLCV candles.

    Parameters:
      candles        — list of dicts with keys: ts, open, high, low, close, volume
      adx_trend      — ADX threshold above which market is trending (default 25)
      adx_range      — ADX threshold below which market is ranging (default 15)
      atr_spike_ratio — ATR/avg_ATR ratio above which market is volatile (default 2.0)
      ema_fast       — fast EMA period for direction detection (default 20)
      ema_slow       — slow EMA period for direction detection (default 50)

    Returns dict with regime, direction, adx, atr, atr_ratio, ema_fast, ema_slow.
    Returns None if insufficient data.
    """
    if len(candles) < max(ema_slow, 2 * adx_period) + 5:
        return None

    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]

    # Compute indicators
    adx_vals, plus_di, minus_di = compute_adx(highs, lows, closes, adx_period)
    atr_vals                    = compute_atr(highs, lows, closes, atr_period)
    ema_f                       = ema(closes, ema_fast)
    ema_s                       = ema(closes, ema_slow)

    # Latest valid values
    adx_now = next((v for v in reversed(adx_vals) if v and v > 0), 0.0)
    atr_now = next((v for v in reversed(atr_vals) if v and v > 0), None)

    # ATR spike check — compare current ATR to 20-period avg ATR
    atr_ratio = 1.0
    if atr_now:
        recent_atrs = [v for v in atr_vals[-25:] if v and v > 0]
        if len(recent_atrs) >= 5:
            avg_atr   = sum(recent_atrs[:-1]) / max(1, len(recent_atrs) - 1)
            atr_ratio = atr_now / avg_atr if avg_atr > 0 else 1.0

    # Latest EMA values
    ef_now = next((v for v in reversed(ema_f) if v), None)
    es_now = next((v for v in reversed(ema_s) if v), None)

    # Direction from EMA relationship + plus/minus DI
    pd_now = next((v for v in reversed(plus_di)  if v > 0), 0.0)
    md_now = next((v for v in reversed(minus_di) if v > 0), 0.0)

    if ef_now and es_now:
        if ef_now > es_now and pd_now > md_now:
            direction = "up"
        elif ef_now < es_now and md_now > pd_now:
            direction = "down"
        else:
            direction = "flat"
    else:
        direction = "flat"

    # Regime classification — volatile takes priority
    if atr_ratio >= atr_spike_ratio:
        regime = "volatile"
    elif adx_now >= adx_trend:
        regime = "trending"
    elif adx_now <= adx_range:
        regime = "ranging"
    else:
        regime = "ranging"   # grey zone — treat conservatively as ranging

    return {
        "regime":    regime,
        "direction": direction,
        "adx":       round(adx_now, 2),
        "atr":       round(atr_now, 4) if atr_now else None,
        "atr_ratio": round(atr_ratio, 2),
        "ema_fast":  round(ef_now, 4)  if ef_now else None,
        "ema_slow":  round(es_now, 4)  if es_now else None,
        "plus_di":   round(pd_now, 2),
        "minus_di":  round(md_now, 2),
    }


# ── Load candles from CSV ─────────────────────────────────────────────────────

def load_csv(path):
    candles = []
    with open(path) as f:
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
    return candles


# ── Standalone CLI ────────────────────────────────────────────────────────────

REGIME_EMOJI = {
    "trending": "📈",
    "ranging":  "↔️ ",
    "volatile": "⚡",
}

DIRECTION_EMOJI = {
    "up":   "🟢",
    "down": "🔴",
    "flat": "⬜",
}

def print_regime(result, label=""):
    if not result:
        print("  ⚠️  Insufficient data for regime detection")
        return
    r = result
    emoji = REGIME_EMOJI.get(r["regime"], "?")
    d_emoji = DIRECTION_EMOJI.get(r["direction"], "")
    print(f"\n  {'─'*45}")
    if label:
        print(f"  {label}")
    print(f"  Regime    : {emoji} {r['regime'].upper()}")
    print(f"  Direction : {d_emoji} {r['direction'].upper()}")
    print(f"  ADX       : {r['adx']:.1f}  (+DI={r['plus_di']:.1f}, -DI={r['minus_di']:.1f})")
    print(f"  ATR       : {r['atr']}  (ratio={r['atr_ratio']:.2f}x)")
    print(f"  EMA fast  : {r['ema_fast']}")
    print(f"  EMA slow  : {r['ema_slow']}")
    print(f"  {'─'*45}")

    strategy = {
        "trending": "→ Run EMA crossover trend-follow strategy",
        "ranging":  "→ Run Grid bot strategy",
        "volatile": "→ PAUSE — tighten stops, reduce position size",
    }.get(r["regime"], "")
    print(f"  {strategy}\n")


def main():
    parser = argparse.ArgumentParser(description="Algo Trader — Regime Detector")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--adx-trend",  type=float, default=25,  help="ADX trending threshold (default 25)")
    parser.add_argument("--adx-range",  type=float, default=15,  help="ADX ranging threshold (default 15)")
    parser.add_argument("--atr-spike",  type=float, default=2.0, help="ATR spike ratio (default 2.0)")
    parser.add_argument("--ema-fast",   type=int,   default=20,  help="Fast EMA period (default 20)")
    parser.add_argument("--ema-slow",   type=int,   default=50,  help="Slow EMA period (default 50)")
    parser.add_argument("--json",       action="store_true",      help="Output as JSON")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"❌ File not found: {csv_path}")
        return

    candles = load_csv(csv_path)
    print(f"  Loaded {len(candles)} candles from {csv_path.name}")

    result = detect_regime(
        candles,
        adx_trend      = args.adx_trend,
        adx_range      = args.adx_range,
        atr_spike_ratio= args.atr_spike,
        ema_fast       = args.ema_fast,
        ema_slow       = args.ema_slow,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_regime(result, label=csv_path.stem)


if __name__ == "__main__":
    main()
