import json
from datetime import datetime
from pathlib import Path
import random

from data_engine.fetcher import DataFetcher
from data_engine.validator import DataValidator

from backtest_engine.rsi_strategy import RSIStrategy
from backtest_engine.ema_strategy import EMAStrategy
from backtest_engine.metrics import Metrics
from backtest_engine.validator import StrategyValidator
from backtest_engine.strategy_generator import StrategyGenerator
from backtest_engine.memory import StrategyMemory

from notifier.telegram import send_message


RESULT_DIR = Path("storage/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run_strategy(strategy_config, df):

    if strategy_config["type"] == "RSI":
        strategy = RSIStrategy(**strategy_config["params"])
        df = strategy.calculate_rsi(df.copy())
        trades = strategy.generate_signals(df)

    elif strategy_config["type"] == "EMA":
        strategy = EMAStrategy(**strategy_config["params"])
        df = strategy.calculate_ema(df.copy())
        trades = strategy.generate_signals(df)

    else:
        return None

    metrics = Metrics().calculate(trades)
    validation = StrategyValidator(trades).final_verdict()

    if metrics["max_drawdown"] == 0:
        return None

    score = metrics["total_pnl"] / abs(metrics["max_drawdown"])

    return {
        "strategy": strategy_config,
        "metrics": metrics,
        "validation": validation,
        "score": score
    }


def format_strategy(s):
    if s["type"] == "RSI":
        p = s["params"]
        return f"RSI({p['period']},{p['entry']},{p['exit']})"
    if s["type"] == "EMA":
        p = s["params"]
        return f"EMA({p['fast']},{p['slow']})"
    return str(s)


def main():

    try:
        log("Starting HYBRID strategy engine...")

        fetcher = DataFetcher()
        df = fetcher.get_data("nifty")

        DataValidator().validate(df)

        generator = StrategyGenerator()
        memory = StrategyMemory()

        results = []
        seen = set()

        # -------- EXPLORE (20 new) --------
        MAX_NEW = 20
        attempts = 0

        while len(results) < MAX_NEW and attempts < 200:
            attempts += 1

            strategy_config = generator.generate()
            key = str(strategy_config)

            if key in seen or memory.exists(strategy_config):
                continue

            seen.add(key)

            result = run_strategy(strategy_config, df)

            if result:
                results.append(result)

                memory.add(
                    strategy_config,
                    result["score"],
                    datetime.now().strftime("%Y%m%d_%H%M%S")
                )

        # -------- EXPLOIT (top 10 from memory) --------
        top_memory = memory.top_strategies(10)

        for m in top_memory:
            strategy_config = m["strategy"]

            result = run_strategy(strategy_config, df)

            if result:
                results.append(result)

        if not results:
            log("No strategies found")
            return

        results = sorted(results, key=lambda x: x["score"], reverse=True)
        top_results = results[:3]

        # -------- TELEGRAM --------
        message = "📊 HYBRID STRATEGY REPORT\n\n"

        for i, res in enumerate(top_results, 1):
            message += f"{i}. {format_strategy(res['strategy'])}\n"
            message += f"Score: {round(res['score'], 2)}\n"
            message += f"PnL: {round(res['metrics']['total_pnl'], 2)}\n"
            message += f"DD: {round(res['metrics']['max_drawdown'], 2)}\n\n"

        send_message(message)

        print(message)

    except Exception as e:
        log(f"[ERROR] {e}")
        send_message(f"❌ ERROR: {e}")


if __name__ == "__main__":
    main()
