#!/usr/bin/env python3

import yfinance as yf
import pandas as pd

START_DATE = "2018-01-01"
INVEST_PER_TRADE = 3000


# ───────────────────────── LOAD ETF LIST ─────────────────────────
def load_etfs():
    with open("etf_list.txt") as f:
        return [x.strip() for x in f if x.strip()]


# ───────────────────────── FILTER VALID ETFs ─────────────────────────
def get_valid_etfs(etfs):
    valid = []

    print("🔍 Filtering valid ETFs...\n")

    for symbol in etfs:
        try:
            df = yf.download(symbol, period="5d", progress=False)
        except:
            continue

        if not df.empty:
            print(f"✅ {symbol}")
            valid.append(symbol)
        else:
            print(f"❌ {symbol}")

    print(f"\n🎯 Valid ETFs: {len(valid)}\n")
    return valid


# ───────────────────────── DOWNLOAD DATA ─────────────────────────
def download_data(valid_etfs):
    all_data = []

    print("\n📥 Downloading historical data...\n")

    for symbol in valid_etfs:
        print(f"📊 {symbol}")

        try:
            df = yf.download(symbol, start=START_DATE, progress=False)
        except:
            continue

        if df.empty:
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        df["etf"] = symbol
        df["date"] = pd.to_datetime(df["date"])

        all_data.append(df[["date", "etf", "close"]])

    df = pd.concat(all_data)
    df.sort_values("date", inplace=True)
    df.dropna(inplace=True)

    return df


# ───────────────────────── BACKTEST CORE ─────────────────────────
def run_backtest(df, capital, bid_thresh, max_bid, base, decay, floor):

    cash = capital
    holdings = {}

    equity_curve = []
    min_cash = capital
    trades = 0

    for _, row in df.iterrows():
        code = row["etf"]
        price = row["close"]

        # ENTRY
        if code not in holdings:
            qty = int(INVEST_PER_TRADE // price)

            if qty > 0 and cash >= qty * price:
                holdings[code] = {
                    "qty": qty,
                    "avg": price,
                    "bid_count": 0,
                    "invested": qty * price
                }
                cash -= qty * price
                trades += 1
            continue

        h = holdings[code]

        # SELL
        target = max(floor, base - decay * h["bid_count"])

        if price >= h["avg"] * (1 + target):
            cash += h["qty"] * price
            del holdings[code]
            trades += 1
            continue

        # BID
        if price <= h["avg"] * (1 - bid_thresh):

            if h["bid_count"] < max_bid:

                amt = h["invested"] / 2
                qty = int(amt // price)

                if qty > 0 and cash >= qty * price:
                    total_cost = h["avg"] * h["qty"] + qty * price
                    total_qty = h["qty"] + qty

                    h["avg"] = total_cost / total_qty
                    h["qty"] = total_qty
                    h["invested"] += qty * price
                    h["bid_count"] += 1

                    cash -= qty * price
                    trades += 1

        # equity
        total = cash
        for c, pos in holdings.items():
            last_price = price if c == code else pos["avg"]
            total += pos["qty"] * last_price

        equity_curve.append(total)
        min_cash = min(min_cash, cash)

    final_value = equity_curve[-1] if equity_curve else capital

    # drawdown
    peak = equity_curve[0] if equity_curve else capital
    max_dd = 0

    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak
        max_dd = max(max_dd, dd)

    return {
        "final": int(final_value),
        "drawdown": round(max_dd * 100, 2),
        "min_cash": int(min_cash),
        "trades": trades
    }


# ───────────────────────── OPTIMIZATION ─────────────────────────
def optimize(df):

    CAPITALS = [100000]
    BID_THRESHOLDS = [0.02]
    MAX_BIDS = [3]
    BASES = [0.0628]
    DECAYS = [0.008]
    FLOORS = [0.03]

    best = None

    for cap in CAPITALS:
        for bt in BID_THRESHOLDS:
            for mb in MAX_BIDS:
                for base in BASES:
                    for d in DECAYS:
                        for f in FLOORS:

                            res = run_backtest(df, cap, bt, mb, base, d, f)

                            print(f"Test → {res}")

                            best = {
                                "params": (cap, bt, mb, base, d, f),
                                "result": res
                            }

    return best


# ───────────────────────── CRASH TEST ─────────────────────────
def run_crash_tests(df, params):

    scenarios = {
        "COVID_2020": ("2020-01-01", "2020-06-30"),
        "BEAR_2022": ("2022-01-01", "2022-12-31"),
    }

    for name, (start, end) in scenarios.items():

        print(f"\n🚨 {name} ({start} → {end})\n")

        sub_df = df[(df["date"] >= start) & (df["date"] <= end)]

        res = run_backtest(
            sub_df,
            params[0],
            params[1],
            params[2],
            params[3],
            params[4],
            params[5]
        )

        print(f"📊 RESULT → {res}")


# ───────────────────────── MAIN ─────────────────────────
if __name__ == "__main__":

    etfs = load_etfs()
    valid_etfs = get_valid_etfs(etfs)

    df = download_data(valid_etfs)

    print("\n🚀 Running full backtest...\n")

    best = optimize(df)

    print("\n🏆 BEST CONFIG")
    print(best)

    print("\n🚨 Running crash simulations...\n")

    run_crash_tests(df, best["params"])
