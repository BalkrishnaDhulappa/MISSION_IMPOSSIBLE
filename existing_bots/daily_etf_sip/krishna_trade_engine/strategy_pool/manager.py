import json
import random
from datetime import datetime, timedelta
from pathlib import Path

STRATEGY_FILE = "strategy_pool/strategies.json"
HISTORY_FILE = "strategy_pool/tested_history.json"

RETEST_DAYS = 3  # avoid retesting within 3 days


class StrategyManager:

    def __init__(self):
        self.strategies = self._load_strategies()
        self.history = self._load_history()

    def _load_strategies(self):
        with open(STRATEGY_FILE, "r") as f:
            return json.load(f)

    def _load_history(self):
        if not Path(HISTORY_FILE).exists():
            return {}

        with open(HISTORY_FILE, "r") as f:
            return json.load(f)

    def _save_history(self):
        with open(HISTORY_FILE, "w") as f:
            json.dump(self.history, f, indent=4)

    def _is_recently_tested(self, strategy_name):
        if strategy_name not in self.history:
            return False

        last_tested = datetime.strptime(self.history[strategy_name], "%Y-%m-%d")
        return datetime.now() - last_tested < timedelta(days=RETEST_DAYS)

    def pick_strategy(self):
        random.shuffle(self.strategies)

        for strat in self.strategies:
            if not self._is_recently_tested(strat["name"]):
                return strat

        # fallback: all tested recently → pick random
        return random.choice(self.strategies)

    def mark_tested(self, strategy_name):
        self.history[strategy_name] = datetime.now().strftime("%Y-%m-%d")
        self._save_history()
