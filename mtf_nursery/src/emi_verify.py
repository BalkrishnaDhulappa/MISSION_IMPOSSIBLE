"""EMI repay verification (broker read or manual confirm)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EmiStatus(str, Enum):
    SCHEDULED = "scheduled"
    DUE = "due"
    PENDING_REPAY = "pending_repay"
    VERIFIED = "verified"
    OVERDUE = "overdue"


class PaidVia(str, Enum):
    API = "api"
    MANUAL = "manual"


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    funded_drop: float
    reason: str


def estimate_funded_from_mtf_block(mtf_value: float, initial_margin: float) -> float:
    """Estimate funded amount from Kite holdings mtf fields (calibrate in C3)."""
    return round(max(0.0, mtf_value - initial_margin), 2)


def verify_emi_repaid(
    emi_amount: float,
    funded_before: float,
    funded_now: float,
    *,
    tolerance: float = 50.0,
) -> VerifyResult:
    """True when funded dropped by at least emi_amount (within tolerance)."""
    drop = round(funded_before - funded_now, 2)
    if drop + tolerance >= emi_amount:
        return VerifyResult(verified=True, funded_drop=drop, reason="funded_decreased")
    return VerifyResult(
        verified=False,
        funded_drop=drop,
        reason=f"insufficient_drop need>={emi_amount - tolerance:.2f} got={drop:.2f}",
    )
