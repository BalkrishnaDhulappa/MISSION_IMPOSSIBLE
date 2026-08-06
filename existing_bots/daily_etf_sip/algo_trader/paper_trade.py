#!/usr/bin/env python3

import requests
import json
import os
from datetime import datetime
from pathlib import Path

from regime_detector import detect_regime
from strategy_engine import RegimeDispatcher


BASE_DIR = Path(__file__).resolve().parent

STATE_FILE = BASE_DIR / "paper_state.json"
LOG_FILE = BASE_DIR / "paper_log.txt"
ERROR_LOG = BASE_DIR / "paper_error.txt"


SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 200

RISK_PER_TRADE = 0.025


# 🔔 TELEGRAM FROM ENV
BOT_TOKEN = os.getenv("CRYPTO_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CRYPTO_TELEGRAM_CHAT_ID")


def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=5)
    except Exception:
        pass


class PaperTrader:

    def __init__(self):
        self.dispatcher = RegimeDispatcher()
        self.load_state()

    def load_state(self):
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                data = json.load(f)
        else:
            data = {"capital": 50000, "position": None}

        self.capital = data["capital"]
        self.position = data["position"]

    def save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "capital": self.capital,
                "position": self.position
            }, f, indent=2)

    def fetch_data(self):
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": LIMIT
        }

        data = requests.get(url, params=params, timeout=10).json()

        candles = []
        for d in data:
            candles.append({
                "open": float(d[1]),
                "high": float(d[2]),
                "low": float(d[3]),
                "close": float(d[4])
            })

        return candles

    def log(self, msg, error=False):
        print(msg)

        file = ERROR_LOG if error else LOG_FILE

        with open(file, "a") as f:
            f.write(msg + "\n")

        if not error:
            send_telegram(msg)

    def run(self):
        try:
            candles = self.fetch_data()
            regime = detect_regime(candles)

            signals = self.dispatcher.signal(
                candles,
                regime,
                {"grid": self.position, "ema": None, "dca": None}
            )

            signal = signals["grid"]
            price = candles[-1]["close"]

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

            if signal["action"] == "buy" and self.position is None:

                capital_used = self.capital * RISK_PER_TRADE
                qty = capital_used / price

                self.position = {
                    "entry": price,
                    "qty": qty
                }

                self.log(
                    f"{timestamp} | BUY BTC | price={price:.2f} | qty={qty:.6f} | capital={self.capital:.2f}"
                )

            elif signal["action"] == "sell" and self.position:

                entry = self.position["entry"]
                qty = self.position["qty"]

                pnl = (price - entry) * qty
                self.capital += pnl

                self.log(
                    f"{timestamp} | SELL BTC | price={price:.2f} | pnl={pnl:.2f} | capital={self.capital:.2f}"
                )

                self.position = None

            self.save_state()

        except Exception as e:
            self.log(f"{datetime.utcnow()} | ERROR: {str(e)}", error=True)


if __name__ == "__main__":
    trader = PaperTrader()
    trader.run()
