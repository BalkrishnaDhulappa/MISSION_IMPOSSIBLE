"""Zerodha MTF cost model (D5/D6)."""

from __future__ import annotations

from dataclasses import dataclass


def brokerage(value: float, *, rate: float = 0.003, cap: float = 20) -> float:
    """Per-order brokerage: min(rate × value, cap)."""
    if value < 0:
        raise ValueError("value must be non-negative")
    return round(min(value * rate, cap), 2)


def pledge_fee(*, per_side: float = 15, gst: float = 1.18, sides: int = 1) -> float:
    """Pledge or unpledge charge per ISIN per event."""
    if sides < 0:
        raise ValueError("sides must be non-negative")
    return round(per_side * gst * sides, 2)


def interest(
    funded_amount: float,
    holding_days: int,
    *,
    daily_rate: float = 0.0004,
) -> float:
    """Interest from T+1: funded × daily_rate × days."""
    if funded_amount < 0:
        raise ValueError("funded_amount must be non-negative")
    if holding_days < 0:
        raise ValueError("holding_days must be non-negative")
    return round(funded_amount * daily_rate * holding_days, 2)


def square_off_fee(*, base: float = 50, gst: float = 1.18) -> float:
    """Broker forced square-off charge per order."""
    return round(base * gst, 2)


@dataclass(frozen=True)
class RoundTripCosts:
    buy_brokerage: float
    sell_brokerage: float
    pledge_both: float
    interest: float
    total: float


def round_trip_costs(
    buy_value: float,
    exit_value: float,
    funded_amount: float,
    holding_days: int,
    *,
    brokerage_rate: float = 0.003,
    brokerage_cap: float = 20,
    pledge_per_side: float = 15,
    gst: float = 1.18,
    interest_daily: float = 0.0004,
) -> RoundTripCosts:
    """Buy + sell brokerage, pledge both sides, and MTF interest."""
    buy_b = brokerage(buy_value, rate=brokerage_rate, cap=brokerage_cap)
    sell_b = brokerage(exit_value, rate=brokerage_rate, cap=brokerage_cap)
    pledge = pledge_fee(per_side=pledge_per_side, gst=gst, sides=2)
    intr = interest(funded_amount, holding_days, daily_rate=interest_daily)
    total = round(buy_b + sell_b + pledge + intr, 2)
    return RoundTripCosts(
        buy_brokerage=buy_b,
        sell_brokerage=sell_b,
        pledge_both=pledge,
        interest=intr,
        total=total,
    )
