"""Fetch OHLC and evaluate Genius Stock CAR for delivered losers."""

from __future__ import annotations

from typing import Any

import pandas as pd

from car import CarCheckResult, CarSignal, evaluate_car_position
from scanner_fetch import _close_series, _high_series, fetch_ohlcv_yfinance, yfinance_ticker


def closes_from_52w_high(df: pd.DataFrame) -> list[float]:
    """Daily closes from 52-week high date through latest (Genius sheet C/D)."""
    if df.empty:
        return []
    close = _close_series(df)
    last_1y = df.tail(252)
    highs = _high_series(last_1y)
    if highs.empty or close.empty:
        return []
    high_date = highs.idxmax()
    series = close.loc[high_date:]
    return [float(x) for x in series.tolist()]


def check_symbol_car(
    symbol: str,
    *,
    avg_cost: float | None = None,
    original_invested: float | None = None,
    fetcher: Any = None,
    rising_days: int = 10,
    average_fraction: float = 0.10,
) -> CarCheckResult | None:
    ticker = yfinance_ticker(symbol)
    if fetcher is not None:
        df = fetcher(ticker)
    else:
        df = fetch_ohlcv_yfinance(ticker)
    if df is None or df.empty:
        return None
    closes = closes_from_52w_high(df)
    if len(closes) < rising_days:
        return None
    cmp = float(_close_series(df).iloc[-1])
    return evaluate_car_position(
        symbol,
        closes,
        cmp=cmp,
        avg_cost=avg_cost,
        original_invested=original_invested,
        rising_days=rising_days,
        average_fraction=average_fraction,
    )


def format_car_telegram(result: CarCheckResult) -> str:
    lines = [
        f"CAR {result.symbol}: {result.signal.value}",
        f"CMP ₹{result.cmp:,.2f}",
    ]
    if result.avg_cost is not None:
        lines.append(f"avg cost ₹{result.avg_cost:,.2f}")
        lines.append("in profit → consider SELL" if result.in_profit else "not in profit yet")
    if result.signal == CarSignal.AVERAGE_OUT and result.average_out_amount:
        lines.append(f"Average Out size ~₹{result.average_out_amount:,.0f} (1/10th original)")
    return " | ".join(lines)
