"""Order executor — dry_run logs only; live blocked by default."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from gates import GateResult


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


class LiveTradingBlocked(RuntimeError):
    pass


def execute_intent(
    intent: OrderIntent,
    *,
    mode: str,
    live_confirm_env: str | None = None,
    config_live_flag: bool = False,
    kite: Any = None,
) -> ExecResult:
    """
    dry_run / paper: never place orders.
    live: requires LIVE_CONFIRM=YES and config flag (not enabled in v1).
    """
    exec_mode = ExecMode(mode)
    if exec_mode in (ExecMode.PAPER, ExecMode.DRY_RUN):
        return ExecResult(
            executed=False,
            mode=exec_mode.value,
            message=(
                f"DRY-RUN intent: {intent.side} {intent.qty} {intent.symbol} "
                f"product={intent.product} reason={intent.reason}"
            ),
        )
    if exec_mode == ExecMode.LIVE:
        import os

        if os.environ.get("LIVE_CONFIRM") != "YES" or not config_live_flag:
            raise LiveTradingBlocked(
                "Live orders blocked. Set mode=dry_run or pass LIVE_CONFIRM=YES with live flag."
            )
        if kite is None:
            raise LiveTradingBlocked("Kite client required for live mode")
        # Live placement deferred to C6 — explicit sign-off
        raise LiveTradingBlocked("Live MTF orders not implemented (C6)")
    raise ValueError(f"Unknown mode: {mode}")


def format_gate_block(side: str, symbol: str, gate: GateResult) -> str:
    reasons = ", ".join(gate.reasons) or "unknown"
    return f"SKIP {side} {symbol}: {reasons}"
