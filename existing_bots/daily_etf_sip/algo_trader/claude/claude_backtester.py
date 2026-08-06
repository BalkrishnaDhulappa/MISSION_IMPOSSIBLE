#!/usr/bin/env python3
"""
Algo Trader — Backtester
Shared backtesting framework. Plugs in any instrument's OHLCV CSV.
Models fees accurately per instrument.

Fee models:
  btc    — Delta Exchange: maker 0.02%, taker 0.05%, funding 0.01% per 8h
  gold   — MCX: brokerage ₹20/order, CTT 0.01% sell-side, exchange 0.0026%
  crude  — MCX: same as gold
  silver — MCX: same as gold

Usage:
  python backtester.py --csv data/btc_1h.csv   --instrument btc
  python backtester.py --csv data/gold_1h.csv  --instrument gold
  python backtester.py --all   (runs all available CSVs in data/)
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from claude_regime_detector import detect_regime, load_csv
from claude_strategy_engine  import RegimeDispatcher, EMAStrategy, GridStrategy, DCAStrategy


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = BASE_DIR / "backtest_results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Fee models ────────────────────────────────────────────────────────────────

FEE_MODELS = {
    "btc": {
        "label":          "BTC Perpetual (Delta Exchange)",
        "maker_pct":      0.0002,    # 0.02%
        "taker_pct":      0.0005,    # 0.05%
        "funding_8h_pct": 0.0001,    # 0.01% per 8h (avg; can be negative)
        "ctt_pct":        0.0,
        "fixed_per_order":0.0,
        "slippage_pct":   0.0005,    # 0.05% slippage buffer
    },
    "gold": {
        "label":           "Gold Mini MCX",
        "maker_pct":       0.0,
        "taker_pct":       0.0,
        "funding_8h_pct":  0.0,
        "ctt_pct":         0.0001,   # 0.01% CTT sell-side only
        "exchange_pct":    0.000026, # 0.0026%
        "fixed_per_order": 20.0,     # ₹20 Zerodha flat brokerage
        "gst_on_brok":     0.18,     # 18% GST on brokerage
        "slippage_pct":    0.0003,
    },
    "crude": {
        "label":           "Crude Oil Mini MCX",
        "maker_pct":       0.0,
        "taker_pct":       0.0,
        "funding_8h_pct":  0.0,
        "ctt_pct":         0.0001,
        "exchange_pct":    0.000026,
        "fixed_per_order": 20.0,
        "gst_on_brok":     0.18,
        "slippage_pct":    0.0005,   # crude is more slippery
    },
    "silver": {
        "label":           "Silver Mini MCX",
        "maker_pct":       0.0,
        "taker_pct":       0.0,
        "funding_8h_pct":  0.0,
        "ctt_pct":         0.0001,
        "exchange_pct":    0.000026,
        "fixed_per_order": 20.0,
        "gst_on_brok":     0.18,
        "slippage_pct":    0.0004,
    },
}


def calc_fee(trade_value, instrument, side="buy", holding_hours=0):
    """
    Calculate total transaction cost for one side of a trade.

    trade_value    — notional value of trade in INR/USDT
    instrument     — key in FEE_MODELS
    side           — "buy" or "sell"
    holding_hours  — hours position was held (for funding rate)
    """
    model = FEE_MODELS.get(instrument, FEE_MODELS["btc"])
    cost  = 0.0

    # Exchange / maker-taker fees (crypto)
    cost += trade_value * model.get("taker_pct", 0)

    # CTT — sell side only (MCX)
    if side == "sell":
        cost += trade_value * model.get("ctt_pct", 0)

    # MCX exchange charges
    cost += trade_value * model.get("exchange_pct", 0)

    # Fixed brokerage (Zerodha ₹20/order)
    brok = model.get("fixed_per_order", 0)
    cost += brok * (1 + model.get("gst_on_brok", 0))

    # Slippage
    cost += trade_value * model.get("slippage_pct", 0)

    # Funding rate (crypto futures — per 8h interval held)
    if holding_hours > 0:
        intervals = holding_hours / 8
        cost += trade_value * model.get("funding_8h_pct", 0) * intervals

    return round(cost, 4)


# ── Backtest engine ───────────────────────────────────────────────────────────

class Backtester:
    """
    Walk-forward backtester.
    Uses a rolling window of candles to simulate regime detection + strategy signals.
    """

    def __init__(self, candles, instrument="btc", initial_capital=100000,
                 lookback=100, regime_kwargs=None, strategy_kwargs=None):
        self.candles         = candles
        self.instrument      = instrument
        self.initial_capital = initial_capital
        self.lookback        = lookback    # candles fed to regime detector each step
        self.regime_kwargs   = regime_kwargs   or {}
        self.strategy_kwargs = strategy_kwargs or {}

        # State
        self.capital   = float(initial_capital)
        self.positions = {"ema": None, "grid": None, "dca": None}
        self.trades    = []
        self.equity_curve = []

        # Strategy dispatcher
        self.dispatcher = RegimeDispatcher(
            ema_strategy  = EMAStrategy(**self.strategy_kwargs.get("ema", {})),
            grid_strategy = GridStrategy(**self.strategy_kwargs.get("grid", {})),
            dca_strategy  = DCAStrategy(**self.strategy_kwargs.get("dca", {})),
        )

    def _open_position(self, strategy_key, signal, candle_idx):
        cmp          = signal["price"]
        qty_pct      = signal["qty_pct"]
        # Cap allocation so we never allocate more than available capital
        capital_used = min(self.capital * qty_pct, self.capital * 0.95)
        if capital_used <= 0 or cmp <= 0:
            return

        qty = capital_used / cmp

        fee = calc_fee(capital_used, self.instrument, side="buy")

        # Deduct capital at open — it's now "in the position"
        self.capital -= (capital_used + fee)

        self.positions[strategy_key] = {
            "side":       "long" if signal["action"] == "buy" else "short",
            "entry":      cmp,
            "qty":        qty,
            "capital_in": capital_used,   # what we put in (excl. fee)
            "sl":         signal.get("sl"),
            "tp":         signal.get("tp"),
            "open_idx":   candle_idx,
            "open_ts":    self.candles[candle_idx]["ts"],
        }

    def _close_position(self, strategy_key, signal, candle_idx):
        pos = self.positions.get(strategy_key)
        if not pos:
            return

        cmp           = signal["price"]
        qty           = pos["qty"]
        entry         = pos["entry"]
        side          = pos["side"]
        capital_in    = pos["capital_in"]
        open_idx      = pos["open_idx"]
        holding_hours = max(1, candle_idx - open_idx)

        # Proceeds from closing the position
        trade_value = qty * cmp
        fee         = calc_fee(trade_value, self.instrument, side="sell",
                               holding_hours=holding_hours)

        if side == "long":
            proceeds = trade_value - fee          # what we get back
        else:
            # Short: we get back capital_in + price_drop_profit - fee
            price_change = entry - cmp            # positive if price fell
            proceeds = capital_in + price_change * qty - fee

        # Raw P&L = proceeds minus what we originally put in
        pnl = proceeds - capital_in

        # Return proceeds to capital (capital_in was already deducted at open)
        self.capital += proceeds

        self.trades.append({
            "strategy":    strategy_key,
            "side":        side,
            "open_ts":     pos["open_ts"],
            "close_ts":    self.candles[candle_idx]["ts"],
            "entry":       round(entry, 4),
            "exit":        round(cmp, 4),
            "qty":         round(qty, 6),
            "pnl":         round(pnl, 2),
            "fee":         round(fee, 2),
            "pnl_pct":     round(pnl / capital_in * 100, 3) if capital_in else 0,
            "holding_hrs": holding_hours,
            "reason":      signal.get("reason", ""),
        })

        self.positions[strategy_key] = None

    def run(self):
        print(f"\n  Backtesting {FEE_MODELS.get(self.instrument, {}).get('label', self.instrument)}")
        print(f"  Candles: {len(self.candles)}  |  Capital: ₹{self.initial_capital:,.0f}  |  Lookback: {self.lookback}")

        start_idx = max(self.lookback, 100)   # need enough history for indicators

        for i in range(start_idx, len(self.candles)):
            window  = self.candles[i - self.lookback: i + 1]
            cmp     = self.candles[i]["close"]
            ts      = self.candles[i]["ts"]

            # Detect regime on rolling window
            regime_result = detect_regime(window, **self.regime_kwargs)
            if not regime_result:
                continue

            # Get signals from dispatcher
            signals = self.dispatcher.signal(window, regime_result, self.positions)

            # Process signals
            for key, sig in signals.items():
                if not sig:
                    continue
                action = sig.get("action", "hold")
                pos    = self.positions.get(key)

                if action in ("buy", "short") and pos is None and sig.get("qty_pct", 0) > 0:
                    self._open_position(key, sig, i)

                elif action in ("sell", "buy") and pos is not None:
                    # sell closes long; buy closes short
                    if (action == "sell" and pos["side"] == "long") or \
                       (action == "buy"  and pos["side"] == "short"):
                        self._close_position(key, sig, i)

            # Track equity: self.capital has open-position capital deducted.
            # Add back current market value of each open position.
            open_value = 0.0
            for key, pos in self.positions.items():
                if pos:
                    qty   = pos["qty"]
                    entry = pos["entry"]
                    cap_in = pos["capital_in"]
                    if pos["side"] == "long":
                        open_value += qty * cmp          # current mkt value
                    else:
                        open_value += cap_in + (entry - cmp) * qty

            self.equity_curve.append({
                "ts":      ts,
                "capital": round(self.capital + open_value, 2),
                "regime":  regime_result["regime"],
            })

        # Close any open positions at last price
        last_cmp = self.candles[-1]["close"]
        for key, pos in self.positions.items():
            if pos:
                fake_sig = {"action": "sell" if pos["side"] == "long" else "buy",
                            "price": last_cmp, "reason": "backtest end — forced close"}
                self._close_position(key, fake_sig, len(self.candles) - 1)

        return self._summary()

    def _summary(self):
        total_trades  = len(self.trades)
        wins          = [t for t in self.trades if t["pnl"] > 0]
        losses        = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl     = sum(t["pnl"] for t in self.trades)
        total_fees    = sum(t["fee"] for t in self.trades)
        win_rate      = len(wins) / total_trades * 100 if total_trades else 0
        avg_win       = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
        avg_loss      = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) \
                        if losses and sum(t["pnl"] for t in losses) != 0 else 999

        # Max drawdown from equity curve
        peak = self.initial_capital
        max_dd = 0.0
        for e in self.equity_curve:
            if e["capital"] > peak:
                peak = e["capital"]
            dd = (peak - e["capital"]) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe (simplified — daily returns from equity curve)
        returns = []
        eq = [e["capital"] for e in self.equity_curve]
        for j in range(1, len(eq)):
            returns.append((eq[j] - eq[j-1]) / eq[j-1])
        if len(returns) > 1:
            import statistics
            avg_r  = statistics.mean(returns)
            std_r  = statistics.stdev(returns)
            sharpe = (avg_r / std_r * (8760 ** 0.5)) if std_r > 0 else 0  # annualised hourly
        else:
            sharpe = 0

        final_capital = self.capital
        total_return  = (final_capital - self.initial_capital) / self.initial_capital * 100

        return {
            "instrument":     self.instrument,
            "initial_capital":self.initial_capital,
            "final_capital":  round(final_capital, 2),
            "total_return_pct": round(total_return, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_fees":     round(total_fees, 2),
            "total_trades":   total_trades,
            "win_rate_pct":   round(win_rate, 1),
            "avg_win":        round(avg_win, 2),
            "avg_loss":       round(avg_loss, 2),
            "profit_factor":  round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe":         round(sharpe, 3),
            "trades":         self.trades,
            "equity_curve":   self.equity_curve,
        }


# ── Print results ─────────────────────────────────────────────────────────────

def print_summary(s):
    label = FEE_MODELS.get(s["instrument"], {}).get("label", s["instrument"])
    print(f"\n  {'═'*55}")
    print(f"  BACKTEST RESULTS — {label}")
    print(f"  {'═'*55}")
    print(f"  Capital     : ₹{s['initial_capital']:>12,.0f}  →  ₹{s['final_capital']:>12,.2f}")
    print(f"  Total return: {s['total_return_pct']:>+.2f}%   (P&L: ₹{s['total_pnl']:,.2f})")
    print(f"  Fees paid   : ₹{s['total_fees']:,.2f}")
    print(f"  Trades      : {s['total_trades']}  |  Win rate: {s['win_rate_pct']:.1f}%")
    print(f"  Avg win     : ₹{s['avg_win']:,.2f}  |  Avg loss: ₹{s['avg_loss']:,.2f}")
    print(f"  Profit factor: {s['profit_factor']:.2f}")
    print(f"  Max drawdown: {s['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe      : {s['sharpe']:.3f}")
    print(f"  {'─'*55}")

    # Sample last 5 trades
    if s["trades"]:
        print(f"\n  Last 5 trades:")
        for t in s["trades"][-5:]:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            print(f"    {emoji} [{t['strategy']:5}] {t['side']:5} "
                  f"{t['open_ts']} → {t['close_ts']}  "
                  f"P&L: ₹{t['pnl']:+,.2f} ({t['pnl_pct']:+.2f}%)  {t['reason']}")
    print()


# ── Save results ──────────────────────────────────────────────────────────────

def save_results(summary, instrument):
    # Save full JSON
    out_json = RESULTS_DIR / f"claude_{instrument}_backtest.json"
    # Don't save full equity curve in JSON (too large) — save trades only
    save_data = {k: v for k, v in summary.items() if k != "equity_curve"}
    out_json.write_text(json.dumps(save_data, indent=2))

    # Save equity curve as CSV
    out_csv = RESULTS_DIR / f"{instrument}_equity.csv"
    with open(out_csv, "w") as f:
        f.write("ts,capital,regime\n")
        for e in summary["equity_curve"]:
            f.write(f"{e['ts']},{e['capital']},{e['regime']}\n")

    # Save trade log as CSV
    out_trades = RESULTS_DIR / f"claude_{instrument}_trades.csv"
    if summary["trades"]:
        keys = summary["trades"][0].keys()
        with open(out_trades, "w") as f:
            f.write(",".join(keys) + "\n")
            for t in summary["trades"]:
                f.write(",".join(str(v) for v in t.values()) + "\n")

    print(f"  💾 Results saved: {out_json.name}, {out_csv.name}, {out_trades.name}")
    return out_json


# ── Main ──────────────────────────────────────────────────────────────────────

INSTRUMENT_CSV_MAP = {
    "btc":    "btc_1h.csv",
    "gold":   "gold_1h.csv",
    "crude":  "crude_1h.csv",
    "silver": "silver_1h.csv",
}


def run_backtest(instrument, capital=100000):
    csv_path = DATA_DIR / INSTRUMENT_CSV_MAP.get(instrument, f"{instrument}_1h.csv")
    if not csv_path.exists():
        print(f"  ❌ Data file not found: {csv_path}")
        print(f"     Run: python data_downloader.py --instrument {instrument}")
        return None

    candles = load_csv(csv_path)
    if len(candles) < 200:
        print(f"  ❌ Insufficient data: {len(candles)} candles (need 200+)")
        return None

    print(f"  Loaded {len(candles)} candles from {csv_path.name}")

    bt = Backtester(
        candles          = candles,
        instrument       = instrument,
        initial_capital  = capital,
        lookback         = 100,
        regime_kwargs    = {"adx_trend": 25, "adx_range": 15, "atr_spike_ratio": 2.0},
        strategy_kwargs  = {
            "ema":  {"ema_fast": 20, "ema_slow": 50, "adx_min": 22,
                     "sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "allow_short": True},
            "grid": {"grid_pct": 0.015, "num_levels": 5, "qty_per_level": 0.10},
            "dca":  {"dip_pct": 0.03, "target_pct": 0.06, "qty_pct": 0.15},
        }
    )

    summary = bt.run()
    print_summary(summary)
    save_results(summary, instrument)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Algo Trader — Backtester")
    parser.add_argument("--csv",        help="Path to OHLCV CSV (overrides --instrument)")
    parser.add_argument("--instrument", choices=list(INSTRUMENT_CSV_MAP.keys()),
                        help="Instrument key")
    parser.add_argument("--all",        action="store_true", help="Backtest all instruments")
    parser.add_argument("--capital",    type=float, default=100000, help="Initial capital (default 100000)")
    args = parser.parse_args()

    if args.all:
        results = {}
        for inst in INSTRUMENT_CSV_MAP:
            r = run_backtest(inst, capital=args.capital)
            if r:
                results[inst] = {
                    "return_pct":    r["total_return_pct"],
                    "win_rate":      r["win_rate_pct"],
                    "max_drawdown":  r["max_drawdown_pct"],
                    "sharpe":        r["sharpe"],
                    "profit_factor": r["profit_factor"],
                }

        if results:
            print("\n  ┌─────────────────── SUMMARY TABLE ───────────────────┐")
            print(f"  {'Instrument':<10} {'Return%':>8} {'WinRate':>8} {'MaxDD%':>8} {'Sharpe':>8} {'PF':>6}")
            print("  ├──────────────────────────────────────────────────────┤")
            for inst, r in results.items():
                print(f"  {inst:<10} {r['return_pct']:>+7.2f}% {r['win_rate']:>7.1f}% "
                      f"{r['max_drawdown']:>7.2f}% {r['sharpe']:>8.3f} {r['profit_factor']:>6.2f}")
            print("  └──────────────────────────────────────────────────────┘\n")

    elif args.csv:
        instrument = args.instrument or "btc"
        candles    = load_csv(args.csv)
        bt         = Backtester(candles=candles, instrument=instrument,
                                initial_capital=args.capital)
        summary    = bt.run()
        print_summary(summary)
        save_results(summary, instrument)

    elif args.instrument:
        run_backtest(args.instrument, capital=args.capital)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
