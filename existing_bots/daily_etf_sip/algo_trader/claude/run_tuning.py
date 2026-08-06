#!/usr/bin/env python3
"""
Quick parameter tuning — runs backtester with different EMA configs
and prints a comparison table.
"""
import sys, json
sys.path.insert(0, ".")
from pathlib import Path
from claude_backtester import Backtester
from claude_regime_detector import load_csv

DATA_FILE = Path("../data/btc_1h.csv")
candles   = load_csv(DATA_FILE)
print(f"Loaded {len(candles)} candles\n")

CONFIGS = [
    # label,               ema_fast, ema_slow, adx_min, sl_mult, tp_mult
    ("current (20/50)",         20,   50,  22,  1.5,  3.0),
    ("wider SL/TP (20/50)",     20,   50,  25,  2.0,  4.0),
    ("slower EMA (50/200)",     50,  200,  25,  2.0,  5.0),
    ("high ADX only (20/50)",   20,   50,  30,  2.0,  4.0),
    ("50/200 high ADX",         50,  200,  30,  2.5,  5.0),
]

print(f"{'Config':<26} {'Return%':>8} {'WinRate':>8} {'MaxDD%':>7} "
      f"{'Sharpe':>7} {'EMA_PnL':>10} {'Grid_PnL':>10} {'Trades':>7}")
print("─" * 95)

for label, ef, es, adx, sl, tp in CONFIGS:
    bt = Backtester(
        candles         = candles,
        instrument      = "btc",
        initial_capital = 50000,
        lookback        = 100,
        regime_kwargs   = {"adx_trend": 25, "adx_range": 15, "atr_spike_ratio": 2.0},
        strategy_kwargs = {
            "ema":  {"ema_fast": ef, "ema_slow": es, "adx_min": adx,
                     "sl_atr_mult": sl, "tp_atr_mult": tp, "allow_short": True},
            "grid": {"grid_pct": 0.015, "num_levels": 5, "qty_per_level": 0.10},
            "dca":  {"dip_pct": 0.03, "target_pct": 0.06, "qty_pct": 0.15},
        }
    )
    r = bt.run()

    # Split PnL by strategy
    ema_pnl  = sum(t["pnl"] for t in r["trades"] if t["strategy"] == "ema")
    grid_pnl = sum(t["pnl"] for t in r["trades"] if t["strategy"] == "grid")
    ema_trades = sum(1 for t in r["trades"] if t["strategy"] == "ema")

    print(f"{label:<26} {r['total_return_pct']:>+7.1f}% "
          f"{r['win_rate_pct']:>7.1f}% "
          f"{r['max_drawdown_pct']:>6.1f}% "
          f"{r['sharpe']:>7.2f} "
          f"{ema_pnl:>+10,.0f} "
          f"{grid_pnl:>+10,.0f} "
          f"{ema_trades:>7}")

print("\n✅ Done")
