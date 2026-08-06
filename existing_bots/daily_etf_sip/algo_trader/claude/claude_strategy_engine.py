#!/usr/bin/env python3
"""
Algo Trader — Strategy Engine
Pluggable strategy classes. Each strategy receives candles + regime,
returns a signal dict.

Strategies:
  GridStrategy      — places buy/sell levels in a grid around current price
  EMAStrategy       — trend-follow via EMA crossover + ADX confirmation
  DCAStrategy       — accumulate on dips below moving average

Signal format:
  {
    "action":   "buy" | "sell" | "hold",
    "reason":   str,
    "price":    float,    # suggested limit price
    "qty_pct":  float,    # % of available capital to use (0.0–1.0)
    "strategy": str,      # which strategy fired
    "sl":       float,    # stop loss price
    "tp":       float,    # take profit price
  }
"""

from claude_regime_detector import ema, detect_regime


# ── Base ──────────────────────────────────────────────────────────────────────

class BaseStrategy:
    name = "base"

    def signal(self, candles, regime_result, position=None):
        """
        Override in subclass.
        position: None if flat, else dict {"side": "long"|"short", "entry": float, "qty": float}
        Returns signal dict or None.
        """
        raise NotImplementedError


# ── Grid Strategy ─────────────────────────────────────────────────────────────

class GridStrategy(BaseStrategy):
    """
    Places a grid of buy/sell orders around current price.
    Profits from price oscillation in ranging markets.

    Config:
      grid_pct    — % distance between grid levels (default 1.0%)
      num_levels  — number of levels above and below (default 5)
      qty_per_level — % of capital per grid level (default 0.1 = 10%)
    """
    name = "grid"

    def __init__(self, grid_pct=0.01, num_levels=5, qty_per_level=0.10):
        self.grid_pct      = grid_pct
        self.num_levels    = num_levels
        self.qty_per_level = qty_per_level

    def get_grid_levels(self, cmp):
        """Return list of (price, side) tuples for current price."""
        levels = []
        for i in range(1, self.num_levels + 1):
            buy_price  = round(cmp * (1 - self.grid_pct * i), 6)
            sell_price = round(cmp * (1 + self.grid_pct * i), 6)
            levels.append((buy_price,  "buy"))
            levels.append((sell_price, "sell"))
        levels.sort(key=lambda x: x[0])
        return levels

    def signal(self, candles, regime_result, position=None):
        if not candles or not regime_result:
            return None

        regime = regime_result["regime"]
        cmp    = candles[-1]["close"]

        # Grid only fires in ranging; in volatile regime — hold
        if regime == "volatile":
            return {
                "action":   "hold",
                "reason":   "volatile regime — grid paused",
                "price":    cmp,
                "qty_pct":  0.0,
                "strategy": self.name,
                "sl":       None,
                "tp":       None,
            }

        # In trending regime, grid runs with wider spacing to avoid fighting trend
        effective_pct = self.grid_pct * (2.0 if regime == "trending" else 1.0)
        levels        = self.get_grid_levels(cmp)

        # Find nearest buy level below CMP
        buy_levels  = [(p, s) for p, s in levels if s == "buy"  and p < cmp]
        sell_levels = [(p, s) for p, s in levels if s == "sell" and p > cmp]

        if not buy_levels:
            return None

        nearest_buy  = max(buy_levels,  key=lambda x: x[0])[0]
        nearest_sell = min(sell_levels, key=lambda x: x[0])[0] if sell_levels else round(cmp * (1 + effective_pct), 6)

        # If no open position — signal to place next buy grid level
        if position is None:
            return {
                "action":   "buy",
                "reason":   f"grid buy level {nearest_buy:.2f} (grid_pct={self.grid_pct*100:.1f}%)",
                "price":    nearest_buy,
                "qty_pct":  self.qty_per_level,
                "strategy": self.name,
                "sl":       round(nearest_buy * (1 - self.grid_pct * 2), 6),
                "tp":       nearest_sell,
            }

        # If long position open — check if TP hit
        if position and position.get("side") == "long":
            entry = position["entry"]
            tp    = round(entry * (1 + self.grid_pct), 6)
            sl    = round(entry * (1 - self.grid_pct * 2), 6)
            if cmp >= tp:
                return {
                    "action":   "sell",
                    "reason":   f"grid TP hit ({tp:.2f})",
                    "price":    cmp,
                    "qty_pct":  1.0,
                    "strategy": self.name,
                    "sl":       sl,
                    "tp":       tp,
                }
            if cmp <= sl:
                return {
                    "action":   "sell",
                    "reason":   f"grid SL hit ({sl:.2f})",
                    "price":    cmp,
                    "qty_pct":  1.0,
                    "strategy": self.name,
                    "sl":       sl,
                    "tp":       tp,
                }

        return {"action": "hold", "reason": "grid — awaiting level", "price": cmp,
                "qty_pct": 0.0, "strategy": self.name, "sl": None, "tp": None}


# ── EMA Trend Strategy ────────────────────────────────────────────────────────

class EMAStrategy(BaseStrategy):
    """
    Trend-following via EMA crossover with ADX confirmation.
    Goes long on golden cross (fast > slow) in trending regime.
    Goes short on death cross (fast < slow) in trending regime.
    Exits when cross reverses or regime turns volatile.

    Config:
      ema_fast    — fast EMA period (default 20)
      ema_slow    — slow EMA period (default 50)
      adx_min     — minimum ADX to enter (default 22)
      sl_atr_mult — stop loss = entry ± ATR * this multiplier (default 1.5)
      tp_atr_mult — take profit = entry ± ATR * this multiplier (default 3.0)
      allow_short — allow short positions (default True)
    """
    name = "ema_trend"

    def __init__(self, ema_fast=20, ema_slow=50, adx_min=22,
                 sl_atr_mult=1.5, tp_atr_mult=3.0, allow_short=True):
        self.ema_fast    = ema_fast
        self.ema_slow    = ema_slow
        self.adx_min     = adx_min
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.allow_short = allow_short

    def _ema_cross(self, candles):
        """Returns (fast_prev, slow_prev, fast_now, slow_now)."""
        closes  = [c["close"] for c in candles]
        ef      = ema(closes, self.ema_fast)
        es      = ema(closes, self.ema_slow)

        # Get last two valid pairs
        pairs = [(ef[i], es[i]) for i in range(len(closes))
                 if ef[i] is not None and es[i] is not None]
        if len(pairs) < 2:
            return None
        return pairs[-2][0], pairs[-2][1], pairs[-1][0], pairs[-1][1]

    def signal(self, candles, regime_result, position=None):
        if not candles or not regime_result:
            return None

        regime    = regime_result["regime"]
        adx_now   = regime_result["adx"]
        atr_now   = regime_result.get("atr") or (candles[-1]["high"] - candles[-1]["low"])
        cmp       = candles[-1]["close"]
        direction = regime_result["direction"]

        # Exit immediately on volatile regime if holding
        if regime == "volatile" and position:
            return {
                "action":   "sell" if position["side"] == "long" else "buy",
                "reason":   "volatile regime — emergency exit",
                "price":    cmp,
                "qty_pct":  1.0,
                "strategy": self.name,
                "sl":       None,
                "tp":       None,
            }

        cross = self._ema_cross(candles)
        if not cross:
            return None
        ef_prev, es_prev, ef_now, es_now = cross

        golden_cross = ef_prev <= es_prev and ef_now > es_now   # fast crosses above slow
        death_cross  = ef_prev >= es_prev and ef_now < es_now   # fast crosses below slow

        # ── Entry ──────────────────────────────────────────────────────────
        if position is None:
            # Only enter in trending regime with sufficient ADX
            if regime != "trending" or adx_now < self.adx_min:
                return {"action": "hold", "reason": f"no trend (ADX={adx_now:.1f})",
                        "price": cmp, "qty_pct": 0.0, "strategy": self.name,
                        "sl": None, "tp": None}

            if golden_cross:
                sl = round(cmp - atr_now * self.sl_atr_mult, 6)
                tp = round(cmp + atr_now * self.tp_atr_mult, 6)
                return {
                    "action":   "buy",
                    "reason":   f"golden cross EMA{self.ema_fast}/EMA{self.ema_slow} (ADX={adx_now:.1f})",
                    "price":    cmp,
                    "qty_pct":  0.5,   # use 50% of capital on trend entry
                    "strategy": self.name,
                    "sl":       sl,
                    "tp":       tp,
                }

            if death_cross and self.allow_short:
                sl = round(cmp + atr_now * self.sl_atr_mult, 6)
                tp = round(cmp - atr_now * self.tp_atr_mult, 6)
                return {
                    "action":   "short",
                    "reason":   f"death cross EMA{self.ema_fast}/EMA{self.ema_slow} (ADX={adx_now:.1f})",
                    "price":    cmp,
                    "qty_pct":  0.5,
                    "strategy": self.name,
                    "sl":       sl,
                    "tp":       tp,
                }

        # ── Exit ───────────────────────────────────────────────────────────
        if position:
            entry = position["entry"]
            side  = position["side"]
            sl    = position.get("sl", entry * (0.97 if side == "long" else 1.03))
            tp    = position.get("tp", entry * (1.06 if side == "long" else 0.94))

            # SL / TP hit
            if side == "long":
                if cmp <= sl:
                    return {"action": "sell", "reason": f"SL hit ({sl:.2f})",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}
                if cmp >= tp:
                    return {"action": "sell", "reason": f"TP hit ({tp:.2f})",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}
                # Trend reversal — death cross while long
                if death_cross:
                    return {"action": "sell", "reason": "death cross — trend reversal",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}

            elif side == "short":
                if cmp >= sl:
                    return {"action": "buy", "reason": f"SL hit ({sl:.2f})",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}
                if cmp <= tp:
                    return {"action": "buy", "reason": f"TP hit ({tp:.2f})",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}
                if golden_cross:
                    return {"action": "buy", "reason": "golden cross — trend reversal",
                            "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                            "sl": sl, "tp": tp}

        return {"action": "hold", "reason": "awaiting signal",
                "price": cmp, "qty_pct": 0.0, "strategy": self.name,
                "sl": None, "tp": None}


# ── DCA Strategy ──────────────────────────────────────────────────────────────

class DCAStrategy(BaseStrategy):
    """
    Accumulate on dips below 20-period SMA.
    Sell when price recovers above SMA by target_pct.
    Suitable as a slow accumulation layer alongside grid/trend.

    Config:
      dip_pct      — buy when CMP < SMA * (1 - dip_pct) (default 3%)
      target_pct   — sell when CMP > avg_entry * (1 + target_pct) (default 6%)
      sma_period   — SMA period (default 20)
      qty_pct      — % of capital per DCA buy (default 0.15)
    """
    name = "dca"

    def __init__(self, dip_pct=0.03, target_pct=0.06, sma_period=20, qty_pct=0.15):
        self.dip_pct    = dip_pct
        self.target_pct = target_pct
        self.sma_period = sma_period
        self.qty_pct    = qty_pct

    def _sma(self, candles):
        closes = [c["close"] for c in candles]
        window = closes[-self.sma_period:]
        if len(window) < self.sma_period:
            return None
        return sum(window) / self.sma_period

    def signal(self, candles, regime_result, position=None):
        if not candles or not regime_result:
            return None

        cmp    = candles[-1]["close"]
        regime = regime_result["regime"]
        sma    = self._sma(candles)

        if not sma:
            return None

        # Pause during volatile
        if regime == "volatile":
            return {"action": "hold", "reason": "volatile — DCA paused",
                    "price": cmp, "qty_pct": 0.0, "strategy": self.name,
                    "sl": None, "tp": None}

        # Exit check first
        if position:
            entry  = position["entry"]
            target = round(entry * (1 + self.target_pct), 6)
            sl     = round(entry * 0.93, 6)   # hard 7% SL
            if cmp >= target:
                return {"action": "sell", "reason": f"DCA target hit ({target:.2f})",
                        "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                        "sl": sl, "tp": target}
            if cmp <= sl:
                return {"action": "sell", "reason": f"DCA SL hit ({sl:.2f})",
                        "price": cmp, "qty_pct": 1.0, "strategy": self.name,
                        "sl": sl, "tp": target}

        # Entry: dip below SMA
        dip_threshold = sma * (1 - self.dip_pct)
        if cmp <= dip_threshold:
            sl = round(cmp * 0.93, 6)
            tp = round(cmp * (1 + self.target_pct), 6)
            return {
                "action":   "buy",
                "reason":   f"DCA dip: CMP {cmp:.2f} < SMA({self.sma_period}) {sma:.2f} by {((sma-cmp)/sma)*100:.1f}%",
                "price":    cmp,
                "qty_pct":  self.qty_pct,
                "strategy": self.name,
                "sl":       sl,
                "tp":       tp,
            }

        return {"action": "hold", "reason": f"DCA — CMP {cmp:.2f} above dip threshold {dip_threshold:.2f}",
                "price": cmp, "qty_pct": 0.0, "strategy": self.name, "sl": None, "tp": None}


# ── Regime-aware dispatcher ───────────────────────────────────────────────────

class RegimeDispatcher:
    """
    Master dispatcher: selects the right strategy based on current regime.
    Always runs EMA trend strategy — adds grid on top in ranging regime.

    regime = trending  → EMAStrategy (primary)
    regime = ranging   → GridStrategy (primary) + DCAStrategy (secondary)
    regime = volatile  → hold everything
    """

    def __init__(self,
                 ema_strategy=None,
                 grid_strategy=None,
                 dca_strategy=None):
        self.ema  = ema_strategy  or EMAStrategy()
        self.grid = grid_strategy or GridStrategy()
        self.dca  = dca_strategy  or DCAStrategy()

    def signal(self, candles, regime_result, positions=None):
        """
        positions: dict {"ema": position_or_None, "grid": position_or_None, "dca": position_or_None}
        Returns dict {"ema": signal, "grid": signal, "dca": signal}
        """
        if positions is None:
            positions = {"ema": None, "grid": None, "dca": None}

        regime = regime_result["regime"] if regime_result else "volatile"

        signals = {}

        if regime == "volatile":
            # Emergency: signal all open positions to close
            for key, strat in [("ema", self.ema), ("grid", self.grid), ("dca", self.dca)]:
                pos = positions.get(key)
                if pos:
                    signals[key] = strat.signal(candles, regime_result, pos)
                else:
                    signals[key] = {"action": "hold", "reason": "volatile — no new entries",
                                    "price": candles[-1]["close"] if candles else 0,
                                    "qty_pct": 0.0, "strategy": key, "sl": None, "tp": None}
            return signals

        if regime == "trending":
            signals["ema"]  = self.ema.signal(candles, regime_result, positions.get("ema"))
            signals["grid"] = {"action": "hold", "reason": "trending — grid inactive",
                               "price": candles[-1]["close"], "qty_pct": 0.0,
                               "strategy": "grid", "sl": None, "tp": None}
            signals["dca"]  = {"action": "hold", "reason": "trending — DCA inactive",
                               "price": candles[-1]["close"], "qty_pct": 0.0,
                               "strategy": "dca", "sl": None, "tp": None}

        else:   # ranging
            signals["ema"]  = {"action": "hold", "reason": "ranging — EMA inactive",
                               "price": candles[-1]["close"], "qty_pct": 0.0,
                               "strategy": "ema", "sl": None, "tp": None}
            signals["grid"] = self.grid.signal(candles, regime_result, positions.get("grid"))
            signals["dca"]  = self.dca.signal(candles, regime_result, positions.get("dca"))

        return signals
