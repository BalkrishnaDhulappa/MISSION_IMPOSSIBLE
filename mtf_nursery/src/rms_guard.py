"""RMS margin-crunch detection and LIQUIDCASE top-up sizing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RmsSeverity(str, Enum):
    OK = "OK"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class PositionRisk:
    symbol: str
    mtm_value: float
    funded_amount: float


def loss_pct_vs_funded(mtm_value: float, funded_amount: float) -> float:
    """Loss as fraction of funded amount (0 = breakeven, 0.2 = 20% loss)."""
    if funded_amount <= 0:
        raise ValueError("funded_amount must be positive")
    if mtm_value >= funded_amount:
        return 0.0
    return round((funded_amount - mtm_value) / funded_amount, 6)


def position_risk_severity(
    pos: PositionRisk,
    *,
    warn_pct: float = 0.15,
    critical_pct: float = 0.20,
) -> RmsSeverity:
    loss = loss_pct_vs_funded(pos.mtm_value, pos.funded_amount)
    if loss >= critical_pct:
        return RmsSeverity.CRITICAL
    if loss >= warn_pct:
        return RmsSeverity.WARN
    return RmsSeverity.OK


def account_cash_severity(
    free_cash: float,
    required_buffer: float,
) -> RmsSeverity:
    """CRITICAL when cash cannot cover EMI + fire_shop + ops buffer."""
    if free_cash < required_buffer:
        return RmsSeverity.CRITICAL
    if free_cash < required_buffer * 1.1:
        return RmsSeverity.WARN
    return RmsSeverity.OK


def max_severity(*severities: RmsSeverity) -> RmsSeverity:
    order = {RmsSeverity.OK: 0, RmsSeverity.WARN: 1, RmsSeverity.CRITICAL: 2}
    return max(severities, key=lambda s: order[s])


@dataclass(frozen=True)
class LiquidTopUpPlan:
    cash_shortfall: float
    sell_amount: float
    capped: bool
    below_min_reserve: bool
    reasons: tuple[str, ...]


def plan_liquid_topup(
    cash_shortfall: float,
    liquid_etf_value: float,
    *,
    min_reserve: float,
    max_sell_per_event: float,
    cushion_pct: float = 0.02,
    fire_shop_reserve: float = 0.0,
) -> LiquidTopUpPlan:
    """
    Size a LIQUIDCASE CNC sell to cover margin crunch.

    Respects min_reserve, max per event, and does not sell below reserve.
    """
    reasons: list[str] = []
    if cash_shortfall <= 0:
        return LiquidTopUpPlan(
            cash_shortfall=0.0,
            sell_amount=0.0,
            capped=False,
            below_min_reserve=False,
            reasons=("no_shortfall",),
        )

    target = round(cash_shortfall * (1 + cushion_pct) + fire_shop_reserve, 2)
    available = max(0.0, liquid_etf_value - min_reserve)
    if available <= 0:
        return LiquidTopUpPlan(
            cash_shortfall=cash_shortfall,
            sell_amount=0.0,
            capped=False,
            below_min_reserve=True,
            reasons=("liquid_below_min_reserve",),
        )

    sell_amount = min(target, available, max_sell_per_event)
    capped = sell_amount < target
    insufficient = sell_amount < cash_shortfall
    below_min_reserve = available <= 0 or insufficient
    if capped:
        reasons.append("capped_by_limit_or_available")
    if insufficient:
        reasons.append("insufficient_liquid_above_reserve")
    return LiquidTopUpPlan(
        cash_shortfall=cash_shortfall,
        sell_amount=round(sell_amount, 2),
        capped=capped,
        below_min_reserve=below_min_reserve,
        reasons=tuple(reasons) if reasons else ("ok",),
    )
