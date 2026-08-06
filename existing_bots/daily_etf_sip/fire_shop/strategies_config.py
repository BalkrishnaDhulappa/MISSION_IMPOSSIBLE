"""
strategies_config.py — Define & add your trading strategies here
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Each strategy is a dict with these keys:
  description    : str   — what the strategy does
  entry          : lambda df -> pd.Series[bool]  — True = enter long
  exit           : lambda df -> pd.Series[bool]  — True = exit position
  stop_loss      : float | None  — e.g. 0.07 = 7% hard stop below entry price
  take_profit    : float | None  — e.g. 0.20 = 20% profit target
  trailing_stop  : float | None  — e.g. 0.09 = 9% below highest price seen
  max_hold_days  : int           — force-exit after N calendar days

Available columns in df (all computed from real OHLCV):
  Price:     open, high, low, close, volume
  SMA:       sma5  sma10  sma20  sma50  sma100  sma200
  EMA:       ema9  ema12  ema21  ema26  ema50
  Momentum:  rsi   stoch_k   stoch_d
  Trend:     macd  macd_signal  macd_hist  adx  plus_di  minus_di
  Volatility:bb_upper  bb_lower  bb_mid  bb_pct  atr
  Volume:    vol_ma
  Other:     high_52w  (rolling 252-day high)

Helper functions available: cross_above(s1, s2), cross_below(s1, s2)

Tips:
  - Use .shift(1) to get previous bar's value
  - chain conditions with &  (AND) and |  (OR), not 'and'/'or'
  - wrap scalar comparisons: pd.Series(30, index=df.index) for constant
  - Add a new strategy by adding a key to the STRATEGIES dict below
"""

import pandas as pd

# ── Helpers ───────────────────────────────────────────────────────────────────
def cross_above(s1: pd.Series, s2) -> pd.Series:
    """True on the bar where s1 crosses from below to above s2."""
    if isinstance(s2, (int, float)):
        s2 = pd.Series(s2, index=s1.index)
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))

def cross_below(s1: pd.Series, s2) -> pd.Series:
    """True on the bar where s1 crosses from above to below s2."""
    if isinstance(s2, (int, float)):
        s2 = pd.Series(s2, index=s1.index)
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))

# ── STRATEGY DEFINITIONS ──────────────────────────────────────────────────────
STRATEGIES = {

    # ╔══════════════════════════╗
    # ║   TREND FOLLOWING        ║
    # ╚══════════════════════════╝

    "Golden Cross": {
        "description": "SMA50 crosses above SMA200 + RSI not overbought",
        "entry": lambda df: (
            cross_above(df["sma50"], df["sma200"]) &
            (df["rsi"] > 40) &
            (df["rsi"] < 70)
        ),
        "exit": lambda df: cross_below(df["sma50"], df["sma200"]),
        "stop_loss":    0.07,
        "take_profit":  None,
        "trailing_stop":0.09,
        "max_hold_days":500,
    },

    "MACD Trend": {
        "description": "MACD crosses signal line with price above SMA50 + ADX confirming trend",
        "entry": lambda df: (
            cross_above(df["macd"], df["macd_signal"]) &
            (df["close"] > df["sma50"]) &
            (df["adx"] > 20)
        ),
        "exit": lambda df: cross_below(df["macd"], df["macd_signal"]),
        "stop_loss":    0.06,
        "take_profit":  None,
        "trailing_stop":0.07,
        "max_hold_days":365,
    },

    "Triple MA Momentum": {
        "description": "EMA9 > EMA21 > SMA50 alignment with strong ADX",
        "entry": lambda df: (
            cross_above(df["ema9"], df["ema21"]) &
            (df["ema21"] > df["sma50"]) &
            (df["close"] > df["sma200"]) &
            (df["adx"] > 25)
        ),
        "exit": lambda df: cross_below(df["ema9"], df["ema21"]),
        "stop_loss":    0.05,
        "take_profit":  None,
        "trailing_stop":0.06,
        "max_hold_days":200,
    },

    "ADX Breakout": {
        "description": "Strong trend (ADX>30) with DI+ crossing DI- and price above SMA200",
        "entry": lambda df: (
            cross_above(df["plus_di"], df["minus_di"]) &
            (df["adx"] > 30) &
            (df["close"] > df["sma200"])
        ),
        "exit": lambda df: (
            cross_below(df["plus_di"], df["minus_di"]) |
            (df["adx"] < 20)
        ),
        "stop_loss":    0.07,
        "take_profit":  None,
        "trailing_stop":0.08,
        "max_hold_days":300,
    },

    # ╔══════════════════════════╗
    # ║   MEAN REVERSION         ║
    # ╚══════════════════════════╝

    "RSI Oversold Bounce": {
        "description": "RSI recovers from below 30 with price above SMA100 (structural support)",
        "entry": lambda df: (
            cross_above(df["rsi"], 30) &
            (df["close"] > df["sma100"])
        ),
        "exit": lambda df: df["rsi"] > 70,
        "stop_loss":    0.05,
        "take_profit":  0.20,
        "trailing_stop":None,
        "max_hold_days":120,
    },

    "Bollinger Band Bounce": {
        "description": "Price touches lower BB with RSI<40 — buy the dip in uptrend",
        "entry": lambda df: (
            (df["close"] <= df["bb_lower"]) &
            (df["rsi"] < 40) &
            (df["close"] > df["sma200"])   # only in structurally bullish market
        ),
        "exit": lambda df: (
            (df["close"] >= df["bb_mid"]) |
            (df["rsi"] > 65)
        ),
        "stop_loss":    0.04,
        "take_profit":  0.12,
        "trailing_stop":None,
        "max_hold_days":60,
    },

    "Stochastic Reversal": {
        "description": "Stoch K crosses D from oversold (<25) — short-term bounce play",
        "entry": lambda df: (
            cross_above(df["stoch_k"], df["stoch_d"]) &
            (df["stoch_k"] < 25) &
            (df["close"] > df["sma50"])
        ),
        "exit": lambda df: (
            (df["stoch_k"] > 80) |
            cross_below(df["stoch_k"], df["stoch_d"])
        ),
        "stop_loss":    0.04,
        "take_profit":  0.10,
        "trailing_stop":None,
        "max_hold_days":60,
    },

    # ╔══════════════════════════╗
    # ║   BREAKOUT               ║
    # ╚══════════════════════════╝

    "BB Squeeze Breakout": {
        "description": "Price breaks above upper Bollinger Band with positive MACD histogram",
        "entry": lambda df: (
            (df["close"] > df["bb_upper"]) &
            (df["macd_hist"] > 0) &
            (df["rsi"] > 55)
        ),
        "exit": lambda df: df["close"] < df["bb_mid"],
        "stop_loss":    0.05,
        "take_profit":  0.18,
        "trailing_stop":None,
        "max_hold_days":90,
    },

    "52-Week High Breakout": {
        "description": "Price makes new 52W high on above-average volume — momentum breakout",
        "entry": lambda df: (
            (df["close"] >= df["high_52w"].shift(1)) &
            (df["volume"] > df["vol_ma"] * 1.5) &
            (df["adx"] > 20)
        ),
        "exit": lambda df: df["close"] < df["sma50"],
        "stop_loss":    0.08,
        "take_profit":  None,
        "trailing_stop":0.10,
        "max_hold_days":365,
    },

    # ╔══════════════════════════╗
    # ║   HYBRID / COMBO         ║
    # ╚══════════════════════════╝

    "Trend + RSI Pullback": {
        "description": "Price above SMA200, MACD turns positive, RSI pulls back to 40-55 zone",
        "entry": lambda df: (
            (df["close"] > df["sma200"]) &
            (df["rsi"] > 40) & (df["rsi"] < 55) &
            cross_above(df["macd"], 0)
        ),
        "exit": lambda df: (
            (df["rsi"] > 78) |
            (df["close"] < df["sma50"])
        ),
        "stop_loss":    0.06,
        "take_profit":  None,
        "trailing_stop":0.08,
        "max_hold_days":200,
    },
}

# ── How to add your own strategy ──────────────────────────────────────────────
#
# Example — "EMA Crossover with Volume":
#
#   "EMA Crossover Vol": {
#       "description": "EMA21 crosses EMA50 with above-average volume surge",
#       "entry": lambda df: (
#           cross_above(df["ema21"], df["ema50"]) &
#           (df["volume"] > df["vol_ma"] * 1.3) &
#           (df["rsi"] > 45)
#       ),
#       "exit": lambda df: cross_below(df["ema21"], df["ema50"]),
#       "stop_loss":    0.06,
#       "take_profit":  None,
#       "trailing_stop":0.07,
#       "max_hold_days":300,
#   },
#
# Then run: python strategy_validator.py --strategy "EMA Crossover Vol"
