import pandas as pd


class Metrics:

    def calculate(self, trades):

        df = pd.DataFrame(trades)

        if df.empty:
            return {"pnl": 0, "win_rate": 0, "max_dd": 0}

        total_pnl = df["pnl"].sum()

        win_rate = (df["pnl"] > 0).mean() * 100

        equity = df["pnl"].cumsum()
        drawdown = equity - equity.cummax()
        max_dd = drawdown.min()

        return {
            "total_pnl": total_pnl,
            "win_rate": round(win_rate, 2),
            "max_drawdown": round(max_dd, 2)
        }
