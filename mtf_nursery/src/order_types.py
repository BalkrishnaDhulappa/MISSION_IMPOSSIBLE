"""Shared order types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecMode(str, Enum):
    PAPER = "paper"
    DRY_RUN = "dry_run"
    LIVE = "live"


@dataclass(frozen=True)
class OrderIntent:
    side: str
    symbol: str
    qty: int
    product: str
    reason: str
    limit_price: float | None = None
    exchange: str = "NSE"


@dataclass(frozen=True)
class ExecResult:
    executed: bool
    mode: str
    message: str
    broker_order_id: str | None = None
