import pandas as pd


class EMAStrategy:

    def __init__(self, fast=10, slow=50):
        self.fast = fast
        self.slow = slow

    def calculate_ema(self, df):
        df["ema_fast"] = df["close"].ewm(span=self.fast).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow).mean()
        return df

    def generate_signals(self, df):
        position = 0
        trades = []

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]

            # Entry (golden crossover)
            if prev["ema_fast"] < prev["ema_slow"] and row["ema_fast"] > row["ema_slow"] and position == 0:
                position = 1
                entry_price = row["close"]
                entry_date = row["date"]

            # Exit (death crossover)
            elif prev["ema_fast"] > prev["ema_slow"] and row["ema_fast"] < row["ema_slow"] and position == 1:
                exit_price = row["close"]
                exit_date = row["date"]

                pnl = exit_price - entry_price

                trades.append({
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl
                })

                position = 0

        return trades
