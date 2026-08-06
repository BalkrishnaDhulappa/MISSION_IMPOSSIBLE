#!/usr/bin/env python3

import yfinance as yf
import pandas as pd

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
ETFS = [
    "ITBEES.NS",
    "NIFTYBEES.NS",
    "BANKBEES.NS",
    "PHARMABEES.NS",
    "METALIETF.NS",
    "CPSEETF.NS",
    "LOWVOLIETF.NS",
    "MOM100.NS",
    "MAFANG.NS",
    "MON100.NS"
]

START_DATE = "2020-01-01"

INITIAL_CAPITAL = 100000
SIP_AMOUNT = 3000
BID_THRESHOLD = 0.025
MAX_BID = 3

# ─────────────────────────────────────────────
# DOWNLOAD DATA (ROBUST)
# ─────────────────────────────────────────────
def download_data():
    all_data = []

    for symbol in ETFS:
        print(f"📥 Downloading {symbol}...")

        df = yf.download(symbol, start=START_DATE, progress=False)

        if df.empty:
            print(f"❌ Skipping {symbol}")
            continue

        # 🔥 FIX: Flatten MultiIndex if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()

        # Standardize column names
        df.columns = [str(c).lower() for c in df.columns]

        if "date" not in df.columns or "close" not in df.columns:
            print(f"❌ Bad format for {symbol}")
            continue

        df["etf"] = "NSE:" + symbol.replace(".NS", "")
        df["date"] = pd.to_datetime(df["date"])

        all_data.append(df[["date", "etf", "close"]])

    final = pd.concat(all_data)
    final.sort_values("date", inplace=True)

    final = final.dropna()
    final["etf"] = final["etf"].astype(str)

    final.to_csv("etf_data.csv", index=False)

    print("\n✅ Data saved to etf_data.csv")

    return final


# ─────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────
def backtest(df, sip_type="monthly"):
    cash = INITIAL_CAPITAL
    holdings = {}
    last_sip_date = {}

    df = df.sort_values("date")

    for _, row in df.iterrows():
        date = row["date"]
        code = row["etf"]
        price = row["close"]

        # INITIAL BUY
        if code not in holdings:
            qty = int(SIP_AMOUNT // price)
            if qty <= 0:
                continue

            holdings[code] = {
                "qty": qty,
                "last_buy": price,
                "bid_count": 0,
                "invested": qty * price
            }

            cash -= qty * price
            continue

        h = holdings[code]

        # BID LOGIC
        if price <= h["last_buy"] * (1 - BID_THRESHOLD):
            if h["bid_count"] < MAX_BID:

                bid_amt = h["invested"] / 2
                qty = int(bid_amt // price)

                if qty > 0 and cash >= qty * price:
                    h["qty"] += qty
                    h["invested"] += qty * price
                    h["last_buy"] = price
                    h["bid_count"] += 1
                    cash -= qty * price

        # SIP LOGIC
        if h["bid_count"] >= MAX_BID:

            do_sip = False

            if code not in last_sip_date:
                do_sip = True
            else:
                last = last_sip_date[code]

                if sip_type == "weekly":
                    if (date - last).days >= 7:
                        do_sip = True
                else:
                    if (date.year, date.month) != (last.year, last.month):
                        do_sip = True

            if do_sip:
                qty = int(SIP_AMOUNT // price)

                if qty > 0 and cash >= qty * price:
                    h["qty"] += qty
                    h["invested"] += qty * price
                    last_sip_date[code] = date
                    cash -= qty * price

    # FINAL VALUE
    total_value = cash

    for code, h in holdings.items():
        last_price = df[df["etf"] == code]["close"].iloc[-1]
        total_value += h["qty"] * last_price

    return total_value


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────
def run():
    df = download_data()

    print("\n🧪 Running backtest...\n")

    weekly = backtest(df, "weekly")
    monthly = backtest(df, "monthly")

    print("\n==============================")
    print("📊 RESULTS")
    print("==============================")
    print(f"Initial Capital : ₹{INITIAL_CAPITAL}")
    print(f"Weekly SIP      : ₹{int(weekly)}")
    print(f"Monthly SIP     : ₹{int(monthly)}")

    if weekly > monthly:
        print("\n🏆 Weekly performs better")
    else:
        print("\n🏆 Monthly performs better")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    run()
