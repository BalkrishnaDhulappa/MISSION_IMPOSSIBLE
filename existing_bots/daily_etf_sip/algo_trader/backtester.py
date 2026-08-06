#!/usr/bin/env python3

import argparse
from pathlib import Path
from collections import defaultdict

from regime_detector import detect_regime, load_csv
from strategy_engine import RegimeDispatcher


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


class Backtester:

    def __init__(self, candles, initial_capital=100000):
        self.candles = candles

        self.initial_capital = float(initial_capital)
        self.available_capital = float(initial_capital)

        self.equity = float(initial_capital)
        self.peak_equity = float(initial_capital)

        self.risk_per_trade = 0.03
        self.max_exposure_pct = 0.3

        # 🔥 realistic costs
        self.fee_rate = 0.0005      # 0.05%
        self.slippage = 0.001       # 0.1%

        self.positions = {"ema": None, "grid": None, "dca": None}
        self.trades = []

        self.dispatcher = RegimeDispatcher()

    def current_exposure(self):
        return sum(pos["capital_in"] for pos in self.positions.values() if pos)

    def _apply_slippage(self, price, side):
        if side == "buy":
            return price * (1 + self.slippage)
        elif side == "sell":
            return price * (1 - self.slippage)
        return price

    def _calculate_unrealized(self, current_price):
        unrealized = 0.0

        for pos in self.positions.values():
            if not pos:
                continue

            entry = pos["entry"]
            qty = pos["qty"]

            if pos["side"] == "long":
                unrealized += (current_price - entry) * qty
            else:
                unrealized += (entry - current_price) * qty

        return unrealized

    def _update_equity(self, current_price):
        unrealized = self._calculate_unrealized(current_price)
        self.equity = self.available_capital + unrealized
        self.peak_equity = max(self.peak_equity, self.equity)

        drawdown = (self.peak_equity - self.equity) / self.peak_equity

        if drawdown > 0.2:
            self.risk_per_trade = 0.015

    def _open_position(self, key, signal, i):
        raw_price = signal["price"]
        price = self._apply_slippage(raw_price, "buy")

        capital_used = self.available_capital * self.risk_per_trade

        if self.current_exposure() + capital_used > self.available_capital * self.max_exposure_pct:
            return

        fee = capital_used * self.fee_rate
        capital_used_after_fee = capital_used - fee

        qty = capital_used_after_fee / price
        self.available_capital -= capital_used

        self.positions[key] = {
            "side": "long" if signal["action"] == "buy" else "short",
            "entry": price,
            "qty": qty,
            "capital_in": capital_used,
            "open_idx": i
        }

    def _close_position(self, key, signal, i):
        pos = self.positions.get(key)
        if not pos:
            return

        raw_price = signal["price"]
        price = self._apply_slippage(raw_price, "sell")

        qty = pos["qty"]
        entry = pos["entry"]

        if pos["side"] == "long":
            pnl = (price - entry) * qty
        else:
            pnl = (entry - price) * qty

        fee = (price * qty) * self.fee_rate
        pnl -= fee

        self.available_capital += pos["capital_in"] + pnl

        self.trades.append({
            "strategy": key,
            "pnl": pnl
        })

        self.positions[key] = None

    def run(self):
        for i in range(100, len(self.candles)):

            window = self.candles[i-100:i+1]
            regime = detect_regime(window)

            current_price = window[-1]["close"]

            self._update_equity(current_price)

            signals = self.dispatcher.signal(window, regime, self.positions)

            for key, sig in signals.items():
                if not sig:
                    continue

                pos = self.positions.get(key)

                if sig["action"] in ("buy", "short") and pos is None:
                    self._open_position(key, sig, i)

                elif sig["action"] in ("sell", "buy") and pos:
                    self._close_position(key, sig, i)

        return self.summary()

    def summary(self):
        final = self.available_capital

        stats = defaultdict(lambda: {"pnl": 0, "count": 0})

        for t in self.trades:
            stats[t["strategy"]]["pnl"] += t["pnl"]
            stats[t["strategy"]]["count"] += 1

        print("\n===== STRATEGY BREAKDOWN =====")
        for k, v in stats.items():
            avg = v["pnl"] / v["count"] if v["count"] else 0
            print(f"{k}: trades={v['count']}, pnl={v['pnl']:.2f}, avg={avg:.2f}")

        return {
            "initial": self.initial_capital,
            "final": round(final, 2),
            "return_pct": round((final - self.initial_capital)/self.initial_capital*100, 2),
            "trades": len(self.trades)
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", default="btc")
    parser.add_argument("--capital", type=float, default=100000)
    args = parser.parse_args()

    csv_path = DATA_DIR / f"{args.instrument}_1h.csv"
    candles = load_csv(csv_path)

    bt = Backtester(candles, args.capital)
    result = bt.run()

    print("\n===== REALISTIC RESULT =====")
    print(result)


if __name__ == "__main__":
    main()
