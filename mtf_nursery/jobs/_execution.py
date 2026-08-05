"""Shared order execution + ledger logging for jobs."""

from __future__ import annotations

from typing import Any

from executor import ExecResult, OrderIntent, execute_intent
from gates import GateResult
from ledger import Ledger
from live_trading import config_live_flag_for_intent, execution_mode_for_liquid, execution_mode_for_mtf


def run_order_with_ledger(
    ledger: Ledger,
    intent: OrderIntent,
    *,
    cfg: dict,
    idempotency_key: str,
    kite: Any = None,
    gate_results: GateResult | None = None,
) -> tuple[ExecResult | None, str, bool]:
    """
    Execute (or dry-run) an order and log to ledger.
    Returns (result, message, already_logged).
    """
    if ledger.has_order_intent(idempotency_key):
        return None, f"Order already logged today: {intent.symbol}", True

    product = intent.product.upper()
    if product == "MTF":
        mode = execution_mode_for_mtf(cfg)
    else:
        mode = execution_mode_for_liquid(cfg)
    live_flag = config_live_flag_for_intent(cfg, product)

    result = execute_intent(
        intent,
        mode=mode,
        config_live_flag=live_flag,
        kite=kite,
        order_tag=cfg.get("live_order_tag"),
    )
    row_id = ledger.log_order_intent(
        intent.side,
        intent.symbol,
        qty=intent.qty,
        product=intent.product,
        mode=mode,
        reason=intent.reason,
        gate_results=gate_results,
        idempotency_key=idempotency_key,
        broker_order_id=result.broker_order_id,
    )
    if row_id is None:
        return result, f"Order already logged today: {intent.symbol}", True
    return result, result.message, False
