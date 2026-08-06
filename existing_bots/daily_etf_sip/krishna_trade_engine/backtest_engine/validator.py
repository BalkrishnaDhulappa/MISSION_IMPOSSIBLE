import pandas as pd


class StrategyValidator:

    def __init__(self, trades):
        self.df = pd.DataFrame(trades)

    def yearly_consistency(self):
        if self.df.empty:
            return {"status": "FAIL", "reason": "No trades"}

        self.df["year"] = pd.to_datetime(self.df["entry_date"]).dt.year

        yearly_pnl = self.df.groupby("year")["pnl"].sum()

        positive_years = (yearly_pnl > 0).sum()
        total_years = len(yearly_pnl)

        consistency_ratio = positive_years / total_years

        return {
            "yearly_pnl": yearly_pnl.to_dict(),
            "consistency_ratio": round(consistency_ratio, 2)
        }

    def drawdown_check(self):
        equity = self.df["pnl"].cumsum()
        drawdown = equity - equity.cummax()
        max_dd = drawdown.min()

        total_pnl = equity.iloc[-1] if not equity.empty else 0

        dd_ratio = abs(max_dd) / total_pnl if total_pnl != 0 else 1

        return {
            "max_drawdown": round(max_dd, 2),
            "dd_ratio": round(dd_ratio, 2)
        }

    def trade_quality(self):
        total_trades = len(self.df)

        if total_trades == 0:
            return {"status": "FAIL", "reason": "No trades"}

        win_rate = (self.df["pnl"] > 0).mean()

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2)
        }

    def final_verdict(self):

        yc = self.yearly_consistency()
        dd = self.drawdown_check()
        tq = self.trade_quality()

        verdict = "PASS"
        reasons = []

        if yc.get("consistency_ratio", 0) < 0.6:
            verdict = "FAIL"
            reasons.append("Low yearly consistency")

        if dd.get("dd_ratio", 1) > 0.3:
            verdict = "FAIL"
            reasons.append("High drawdown")

        if tq.get("total_trades", 0) < 50:
            verdict = "FAIL"
            reasons.append("Too few trades")

        return {
            "verdict": verdict,
            "reasons": reasons,
            "details": {
                "yearly": yc,
                "drawdown": dd,
                "trade_quality": tq
            }
        }
