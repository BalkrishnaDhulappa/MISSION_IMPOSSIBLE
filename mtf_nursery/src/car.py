"""Genius Stock CAR — Average Out / Avoid Hold (sheet + video parity)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from scanner import is_car_rising


class CarSignal(str, Enum):
    AVERAGE_OUT = "BUY / AVERAGE OUT"
    AVOID_HOLD = "AVOID / HOLD"


@dataclass(frozen=True)
class CarCheckResult:
    symbol: str
    signal: CarSignal
    car_last_n: tuple[float, ...]
    cmp: float
    avg_cost: float | None = None
    in_profit: bool = False
    average_out_amount: float | None = None


def cumulative_averages(closes: Sequence[float]) -> tuple[float, ...]:
    """CA(n) = mean of first n closes from 52-week-high date (Genius sheet E column)."""
    if not closes:
        return ()
    out: list[float] = []
    total = 0.0
    for i, px in enumerate(closes, start=1):
        total += float(px)
        out.append(round(total / i, 8))
    return tuple(out)


def car_signal_from_last_n(car_last_n: Sequence[float], *, rising_days: int = 10) -> CarSignal:
    """
    Strictly rising last N CA values → AVERAGE OUT (sheet H2).
    Sheet sorts newest-first and checks G2>G3>...; chronologically that is rising.
    """
    if len(car_last_n) < rising_days:
        return CarSignal.AVOID_HOLD
    tail = list(car_last_n[-rising_days:])
    if is_car_rising(tail):
        return CarSignal.AVERAGE_OUT
    return CarSignal.AVOID_HOLD


def average_out_notional(original_invested: float, *, fraction: float = 0.10) -> float:
    """Weekly add = 1/10th of original invested capital (video)."""
    if original_invested <= 0:
        raise ValueError("original_invested must be positive")
    if fraction <= 0 or fraction > 1:
        raise ValueError("fraction must be in (0, 1]")
    return round(original_invested * fraction, 2)


def qty_for_average_out(notional: float, cmp: float) -> int:
    """Whole shares for average-out add (at least 1 if affordable)."""
    if cmp <= 0:
        raise ValueError("cmp must be positive")
    if notional < cmp:
        return 0  # cannot buy even 1 share this week
    return max(1, int(notional // cmp))


def is_in_profit(cmp: float, avg_cost: float) -> bool:
    """Exit guide (your rule): sell when CMP >= average cost."""
    if avg_cost <= 0:
        raise ValueError("avg_cost must be positive")
    return cmp >= avg_cost


def evaluate_car_position(
    symbol: str,
    closes_from_year_high: Sequence[float],
    *,
    cmp: float | None = None,
    avg_cost: float | None = None,
    original_invested: float | None = None,
    rising_days: int = 10,
    average_fraction: float = 0.10,
) -> CarCheckResult:
    """Full check for one delivered (or watchlist) symbol."""
    cas = cumulative_averages(closes_from_year_high)
    signal = car_signal_from_last_n(cas, rising_days=rising_days)
    last_n = cas[-rising_days:] if len(cas) >= rising_days else cas
    px = float(cmp if cmp is not None else (closes_from_year_high[-1] if closes_from_year_high else 0))
    profit = False
    if avg_cost is not None and px > 0:
        profit = is_in_profit(px, avg_cost)
    add_amt = None
    if signal == CarSignal.AVERAGE_OUT and original_invested is not None:
        add_amt = average_out_notional(original_invested, fraction=average_fraction)
    return CarCheckResult(
        symbol=symbol,
        signal=signal,
        car_last_n=tuple(last_n),
        cmp=px,
        avg_cost=avg_cost,
        in_profit=profit,
        average_out_amount=add_amt,
    )
