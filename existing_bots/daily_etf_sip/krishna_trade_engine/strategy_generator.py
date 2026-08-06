import random


class StrategyGenerator:

    IDEAS = [
        "RSI",
        "EMA",
    ]

    def generate(self):

        idea = random.choice(self.IDEAS)

        if idea == "RSI":
            return {
                "type": "RSI",
                "params": {
                    "period": random.choice([10, 14, 20]),
                    "entry": random.choice([25, 30, 35]),
                    "exit": random.choice([50, 55, 60])
                }
            }

        elif idea == "EMA":
            return {
                "type": "EMA",
                "params": {
                    "fast": random.choice([10, 20]),
                    "slow": random.choice([50, 100])
                }
            }
