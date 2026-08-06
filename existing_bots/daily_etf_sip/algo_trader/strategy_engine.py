#!/usr/bin/env python3

from regime_detector import ema


class GridStrategy:
    name = "grid"

    def __init__(self, grid_pct=0.035, num_levels=3, qty_per_level=0.15):
        self.grid_pct = grid_pct
        self.num_levels = num_levels
        self.qty_per_level = qty_per_level

    def get_levels(self, cmp):
        levels = []
        for i in range(1, self.num_levels + 1):
            levels.append(("buy", cmp * (1 - self.grid_pct * i)))
            levels.append(("sell", cmp * (1 + self.grid_pct * i)))
        return levels

    def signal(self, candles, regime_result, position=None):
        cmp = candles[-1]["close"]

        prev = candles[-2]["close"]
        move = abs(cmp - prev) / cmp

        # 🔥 balanced filter
        if move < 0.003:
            return {"action": "hold", "price": cmp, "qty_pct": 0.0}

        levels = self.get_levels(cmp)
        buy_levels = [p for s, p in levels if s == "buy" and p < cmp]

        if position is None:
            if not buy_levels:
                return None

            return {
                "action": "buy",
                "price": max(buy_levels),
                "qty_pct": self.qty_per_level
            }

        entry = position["entry"]

        if cmp >= entry * (1 + self.grid_pct * 1.3):
            return {"action": "sell", "price": cmp, "qty_pct": 1.0}

        if cmp <= entry * (1 - self.grid_pct * 2):
            return {"action": "sell", "price": cmp, "qty_pct": 1.0}

        return {"action": "hold", "price": cmp, "qty_pct": 0.0}


class EMAStrategy:
    def signal(self, candles, regime_result, position=None):
        return {"action": "hold", "price": candles[-1]["close"], "qty_pct": 0.0}


class DCAStrategy:
    def signal(self, candles, regime_result, position=None):
        return {"action": "hold", "price": candles[-1]["close"], "qty_pct": 0.0}


class RegimeDispatcher:

    def __init__(self, ema_strategy=None, grid_strategy=None, dca_strategy=None):
        self.grid = grid_strategy if grid_strategy else GridStrategy()

    def signal(self, candles, regime_result, positions):
        return {
            "ema": {"action": "hold", "price": candles[-1]["close"], "qty_pct": 0.0},
            "grid": self.grid.signal(candles, regime_result, positions.get("grid")),
            "dca": {"action": "hold", "price": candles[-1]["close"], "qty_pct": 0.0},
        }
