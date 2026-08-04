"""Order executor — dry_run logs only; live behind explicit gates (C6)."""

from __future__ import annotations

import os
from typing import Any

from gates import GateResult
from kite_orders import KiteOrderError, place_kite_order
from live_trading import LiveTradingBlocked, assert_live_allowed
from order_types import ExecMode, ExecResult, OrderIntent

# Re-export for existing imports
__all__ = [
    "ExecMode",
    "ExecResult",
    "LiveTradingBlocked",
    "OrderIntent",
    "execute_intent",
    "format_gate_block",
]


def execute_intent(
    intent: OrderIntent,
    *,
    mode: str,
    live_confirm_env: str | None = None,
    config_live_flag: bool = False,
    kite: Any = None,
    order_tag: str | None = None,
) -> ExecResult:
    """
    dry_run / paper: never place orders.
    live: requires LIVE_CONFIRM=YES and matching config flag.
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
        if live_confirm_env is not None and os.environ.get("LIVE_CONFIRM") != live_confirm_env:
            raise LiveTradingBlocked(
                "Live orders blocked. Set mode=dry_run or pass LIVE_CONFIRM=YES with live flag."
            )
        assert_live_allowed(config_live_flag=config_live_flag, kind=intent.product)
        if kite is None:
            raise LiveTradingBlocked("Kite client required for live mode")
        try:
            order_id = place_kite_order(kite, intent, tag=order_tag)
        except KiteOrderError as exc:
            raise LiveTradingBlocked(f"Kite order failed: {exc}") from exc
        return ExecResult(
            executed=True,
            mode=exec_mode.value,
            message=(
                f"LIVE placed: {intent.side} {intent.qty} {intent.symbol} "
                f"product={intent.product} order_id={order_id}"
            ),
            broker_order_id=order_id,
        )
    raise ValueError(f"Unknown mode: {mode}")


def format_gate_block(side: str, symbol: str, gate: GateResult) -> str:
    reasons = ", ".join(gate.reasons) or "unknown"
    return f"SKIP {side} {symbol}: {reasons}"
