import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests

# -----------------------------
# CONFIG
# -----------------------------
INITIAL_CAPITAL = 10000
RISK_PER_TRADE = 0.005
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 1000
FEE = 0.0005  # 0.05% per trade side

# -----------------------------
# FETCH DATA
# -----------------------------
def get_data(symbol, interval, limit):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = requests.get(url, params=params).json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])

    df["open"] = df["open"].astype(float)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    return df

df = get_data(SYMBOL, INTERVAL, LIMIT)

# -----------------------------
# INDICATORS
# -----------------------------
df['EMA20'] = df['close'].ewm(span=20).mean()
df['EMA200'] = df['close'].ewm(span=200).mean()

delta = df['close'].diff()
gain = (delta.where(delta > 0, 0)).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
rs = gain / loss
df['RSI'] = 100 - (100 / (1 + rs))

df['tr'] = (df[['high','close']].max(axis=1) - df[['low','close']].min(axis=1))
df['ATR'] = df['tr'].rolling(14).mean()

# -----------------------------
# BACKTEST
# -----------------------------
capital = INITIAL_CAPITAL
position = None
entry_price = 0
sl = 0
risk = 0

equity = []
drawdowns = []
trades = []
trade_log = []

peak = capital

for i in range(200, len(df)):
    row = df.iloc[i]

    price = row['close']
    ema20 = row['EMA20']
    ema200 = row['EMA200']
    rsi = row['RSI']
    atr = row['ATR']

    prev_high = df.iloc[i-1]['high']
    prev_low = df.iloc[i-1]['low']

    if np.isnan([ema20, ema200, rsi, atr]).any():
        equity.append(capital)
        continue

    long_trend = price > ema200 and ema20 > ema200
    short_trend = price < ema200 and ema20 < ema200

    # ENTRY
    if position is None:

        if (
            long_trend
            and abs(price - ema20)/ema20 < 0.02
            and 40 < rsi < 60
            and price > prev_high
        ):
            position = "long"
            entry_price = price
            sl = price - 1.2 * atr
            risk = capital * RISK_PER_TRADE

            # Entry fee
            capital -= capital * FEE

        elif (
            short_trend
            and abs(price - ema20)/ema20 < 0.02
            and 40 < rsi < 60
            and price < prev_low
        ):
            position = "short"
            entry_price = price
            sl = price + 1.2 * atr
            risk = capital * RISK_PER_TRADE

            capital -= capital * FEE

    # EXIT
    elif position == "long":
        tp = entry_price + 2 * (entry_price - sl)

        if price <= sl:
            capital -= risk
            capital -= capital * FEE

            trades.append(-1)
            trade_log.append(("LONG", entry_price, price, "LOSS"))

            position = None

        elif price >= tp:
            capital += 2 * risk
            capital -= capital * FEE

            trades.append(1)
            trade_log.append(("LONG", entry_price, price, "WIN"))

            position = None

    elif position == "short":
        tp = entry_price - 2 * (sl - entry_price)

        if price >= sl:
            capital -= risk
            capital -= capital * FEE

            trades.append(-1)
            trade_log.append(("SHORT", entry_price, price, "LOSS"))

            position = None

        elif price <= tp:
            capital += 2 * risk
            capital -= capital * FEE

            trades.append(1)
            trade_log.append(("SHORT", entry_price, price, "WIN"))

            position = None

    equity.append(capital)

    peak = max(peak, capital)
    drawdowns.append((peak - capital) / peak)

# -----------------------------
# METRICS
# -----------------------------
equity_series = pd.Series(equity)

win_rate = trades.count(1) / len(trades) if trades else 0
max_dd = max(drawdowns) if drawdowns else 0
total_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

print("\n===== BACKTEST RESULT (v3) =====")
print(f"Final Capital: {round(capital, 2)}")
print(f"Total Return: {round(total_return, 2)}%")
print(f"Total Trades: {len(trades)}")
print(f"Win Rate: {round(win_rate * 100, 2)}%")
print(f"Max Drawdown: {round(max_dd * 100, 2)}%")

# -----------------------------
# SAVE TRADE LOG
# -----------------------------
log_df = pd.DataFrame(trade_log, columns=["Type", "Entry", "Exit", "Result"])
log_df.to_csv("trade_log.csv", index=False)

# -----------------------------
# VISUALIZATION
# -----------------------------
plt.figure()
plt.plot(equity_series.values)
plt.title("Equity Curve")
plt.xlabel("Time")
plt.ylabel("Capital")
plt.show()

plt.figure()
plt.plot(drawdowns)
plt.title("Drawdown Curve")
plt.xlabel("Time")
plt.ylabel("Drawdown")
plt.show()
