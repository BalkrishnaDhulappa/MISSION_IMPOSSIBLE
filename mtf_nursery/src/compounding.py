"""Compounding, Force / Reserve Force, and sell selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from costs import round_trip_costs


@dataclass(frozen=True)
class CompoundingResult:
    gross_profit: float
    costs: float
    net_before_tax: float
    tax_estimate: float
    net_after_tax: float
    self_dividend: float
    growth: float
    next_ticket: float


def estimate_tax(
    net_before_tax: float,
    *,
    tax_rate: float = 0.2,
    surcharge_multiplier: float = 1.04,
) -> float:
    """Sheet-style tax estimate: (net × rate) × surcharge multiplier."""
    if net_before_tax <= 0:
        return 0.0
    return round(net_before_tax * tax_rate * surcharge_multiplier, 2)


def compound_after_win(
    buy_value: float,
    exit_value: float,
    current_ticket: float,
    funded_amount: float,
    holding_days: int,
    *,
    brokerage_rate: float = 0.003,
    brokerage_cap: float = 20,
    pledge_per_side: float = 15,
    gst: float = 1.18,
    interest_daily: float = 0.0004,
    tax_rate: float = 0.2,
    surcharge_multiplier: float = 1.04,
) -> CompoundingResult:
    """50/50 split after Zerodha costs and tax estimate."""
    gross = round(exit_value - buy_value, 2)
    costs_obj = round_trip_costs(
        buy_value,
        exit_value,
        funded_amount,
        holding_days,
        brokerage_rate=brokerage_rate,
        brokerage_cap=brokerage_cap,
        pledge_per_side=pledge_per_side,
        gst=gst,
        interest_daily=interest_daily,
    )
    net_before_tax = round(gross - costs_obj.total, 2)
    tax = estimate_tax(
        net_before_tax,
        tax_rate=tax_rate,
        surcharge_multiplier=surcharge_multiplier,
    )
    net_after_tax = round(net_before_tax - tax, 2)
    self_div = round(0.5 * net_after_tax, 2)
    growth = round(0.5 * net_after_tax, 2)
    next_ticket = round(current_ticket + growth, 2)
    return CompoundingResult(
        gross_profit=gross,
        costs=costs_obj.total,
        net_before_tax=net_before_tax,
        tax_estimate=tax,
        net_after_tax=net_after_tax,
        self_dividend=self_div,
        growth=growth,
        next_ticket=next_ticket,
    )


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    buy_value: float
    current_value: float


def unrealized_pct(position: OpenPosition) -> float:
    if position.buy_value <= 0:
        raise ValueError("buy_value must be positive")
    return (position.current_value - position.buy_value) / position.buy_value


def pick_sell_candidate(
    positions: Iterable[OpenPosition],
    profit_target_pct: float,
) -> OpenPosition | None:
    """Choose highest unrealized % among positions at or above target."""
    eligible = [p for p in positions if unrealized_pct(p) >= profit_target_pct]
    if not eligible:
        return None
    return max(eligible, key=lambda p: unrealized_pct(p))


@dataclass
class ForceTracker:
    """Track Force (3 advance step) and Reserve Force bookings."""

    force_count: int = 0
    step_no: int = 1

    def record_win(self) -> str:
        """Return 'F' for first 3 wins on step; 'RF' for extras before advance."""
        if self.force_count < 3:
            self.force_count += 1
            return "F"
        return "RF"

    def advance_step_if_ready(self) -> bool:
        """Advance step after 3 Force bookings; reset counter."""
        if self.force_count >= 3:
            self.step_no += 1
            self.force_count = 0
            return True
        return False
