#!/usr/bin/env python3

import requests
import time
from datetime import datetime

from regime_detector import detect_regime
from strategy_engine import RegimeDispatcher


SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 200


class PaperTrader:

    def __init__(self, capital=50000):
        self.capital = capital
        self.position = None
        self.dispatcher = RegimeDispatcher()

    def fetch_data(self):
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": LIMIT
        }

        data = requests.get(url, params=params).json()

        candles = []
        for d in data:
            candles.append({
                "open": float(d[1]),
                "high": float(d[2]),
                "low": float(d[3]),
                "close": float(d[4])
            })

        return candles

    def run(self):
        candles = self.fetch_data()
        regime = detect_regime(candles)

        signals = self.dispatcher.signal(
            candles,
            regime,
            {"grid": self.position, "ema": None, "dca": None}
        )

        signal = signals["grid"]
        price = candles[-1]["close"]

        log = f"{datetime.utcnow()} | price={price} | action={signal['action']}"

        if signal["action"] == "buy" and self.position is None:
            qty = self.capital * 0.03 / price
            self.position = {"entry": price, "qty": qty}
            log += f" | BUY qty={qty:.4f}"

        elif signal["action"] == "sell" and self.position:
            pnl = (price - self.position["entry"]) * self.position["qty"]
            self.capital += pnl
            log += f" | SELL pnl={pnl:.2f} capital={self.capital:.2f}"
            self.position = None

        print(log)

        with open("paper_log.txt", "a") as f:
            f.write(log + "\n")


if __name__ == "__main__":
    trader = PaperTrader()
    trader.run()
