import itertools


class StrategyOptimizer:

    def __init__(self, strategy_class, data):
        self.strategy_class = strategy_class
        self.data = data

    def generate_params(self):
        PARAMS = {
            "period": [10, 14, 20],
            "entry": [25, 30, 35],
            "exit": [50, 55, 60]
        }

        return list(itertools.product(
            PARAMS["period"],
            PARAMS["entry"],
            PARAMS["exit"]
        ))

    def run(self, metrics_class, validator_class):

        results = []

        for period, entry, exit_ in self.generate_params():

            strategy = self.strategy_class(period=period, entry=entry, exit=exit_)
            df = strategy.calculate_rsi(self.data.copy())
            trades = strategy.generate_signals(df)

            metrics = metrics_class().calculate(trades)
            validation = validator_class(trades).final_verdict()

            # Avoid divide by zero
            if metrics["max_drawdown"] == 0:
                continue

            score = metrics["total_pnl"] / abs(metrics["max_drawdown"])

            results.append({
                "params": {
                    "period": period,
                    "entry": entry,
                    "exit": exit_
                },
                "metrics": metrics,
                "validation": validation,
                "score": score
            })

        return sorted(results, key=lambda x: x["score"], reverse=True)
