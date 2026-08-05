"""Buy/sell gates — cash, pace, idempotency helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BuyGateInput:
    free_cash: float
    emi_obligation: float
    fire_shop_reserve: float
    ticket_immediate_need: float
    buys_today: int
    buys_this_month: int
    max_buys_per_day: int
    max_mtf_buys_per_month: int
    buy_blocked_by_rms: bool


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_buy_gate(inp: BuyGateInput) -> GateResult:
    reasons: list[str] = []
    if inp.buy_blocked_by_rms:
        reasons.append("rms_buy_blocked")
    if inp.buys_today >= inp.max_buys_per_day:
        reasons.append("max_buys_per_day")
    if inp.buys_this_month >= inp.max_mtf_buys_per_month:
        reasons.append("max_mtf_buys_per_month")
    deployable = inp.free_cash - inp.fire_shop_reserve - inp.emi_obligation
    if deployable <= inp.ticket_immediate_need:
        reasons.append("insufficient_cash_after_obligation")
    return GateResult(allowed=len(reasons) == 0, reasons=tuple(reasons))


@dataclass(frozen=True)
class SellGateInput:
    sells_today: int
    max_sells_per_day: int
    has_eligible_winner: bool


def evaluate_sell_gate(inp: SellGateInput) -> GateResult:
    reasons: list[str] = []
    if inp.sells_today >= inp.max_sells_per_day:
        reasons.append("max_sells_per_day")
    if not inp.has_eligible_winner:
        reasons.append("no_eligible_winner")
    return GateResult(allowed=len(reasons) == 0, reasons=tuple(reasons))


def count_actions_on_date(action_dates: list[date], on: date) -> int:
    return sum(1 for d in action_dates if d == on)


def count_actions_in_month(action_dates: list[date], year: int, month: int) -> int:
    return sum(1 for d in action_dates if d.year == year and d.month == month)
