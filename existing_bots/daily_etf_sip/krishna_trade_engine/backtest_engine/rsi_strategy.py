import pandas as pd


class RSIStrategy:

    def __init__(self, period=14, entry=30, exit=50):
        self.period = period
        self.entry = entry
        self.exit = exit

    def calculate_rsi(self, df):
        delta = df["close"].diff()

        gain = (delta.where(delta > 0, 0)).rolling(self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(self.period).mean()

        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))

        return df

    def generate_signals(self, df):
        position = 0
        trades = []

        for i in range(len(df)):
            row = df.iloc[i]

            if pd.isna(row["rsi"]):
                continue

            # Entry
            if row["rsi"] < self.entry and position == 0:
                position = 1
                entry_price = row["close"]
                entry_date = row["date"]

            # Exit
            elif row["rsi"] > self.exit and position == 1:
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
