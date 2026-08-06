#!/usr/bin/env python3
import os
import json
from kiteconnect import KiteConnect
from pathlib import Path


TOKEN_FILE = Path(".kite_token")


def get_env(name):
    value = os.getenv(name)
    if not value:
        print(f"❌ Environment variable {name} not set")
        exit(1)
    return value


def load_access_token():
    if not TOKEN_FILE.exists():
        print("❌ .kite_token file not found")
        exit(1)

    data = json.loads(TOKEN_FILE.read_text())
    token = data.get("access_token")

    if not token:
        print("❌ access_token not found in .kite_token")
        exit(1)

    return token


def main():
    api_key = get_env("KITE_API_KEY")
    access_token = load_access_token()

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    print("\nFetching Zerodha holdings...\n")

    try:
        holdings = kite.holdings()

        if not holdings:
            print("No holdings found.")
            return

        total_value = 0
        total_pnl = 0

        print(f"{'Symbol':<15}{'Qty':<8}{'Avg':<10}{'CMP':<10}{'PnL':<10}")
        print("-" * 55)

        for h in holdings:
            symbol = h["tradingsymbol"]
            qty = h["quantity"]
            avg = h["average_price"]
            cmp = h["last_price"]
            pnl = h["pnl"]

            total_value += qty * cmp
            total_pnl += pnl

            print(f"{symbol:<15}{qty:<8}{avg:<10}{cmp:<10}{round(pnl,2):<10}")

        print("-" * 55)
        print(f"Total Value: ₹{round(total_value,2)}")
        print(f"Total PnL : ₹{round(total_pnl,2)}")

    except Exception as e:
        print("Error fetching holdings:", e)


if __name__ == "__main__":
    main()
