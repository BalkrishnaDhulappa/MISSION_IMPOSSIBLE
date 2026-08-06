import random


class StrategyGenerator:

    def generate(self):

        idea = random.choice(["RSI", "EMA"])

        if idea == "RSI":
            return {
                "type": "RSI",
                "params": {
                    "period": random.choice([5, 7, 10, 14, 20, 25]),
                    "entry": random.choice([20, 25, 30, 35, 40]),
                    "exit": random.choice([45, 50, 55, 60, 65, 70])
                }
            }

        elif idea == "EMA":
            return {
                "type": "EMA",
                "params": {
                    "fast": random.choice([5, 10, 15, 20]),
                    "slow": random.choice([30, 50, 100, 150, 200])
                }
            }
