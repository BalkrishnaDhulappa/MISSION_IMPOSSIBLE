"""LIQUIDCASE CNC sell-for-cash — dry-run intent with qty (C4)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from broker_read import AccountSnapshot, build_account_snapshot, liquid_etf_position
from executor import ExecResult, OrderIntent, execute_intent
from ledger import Ledger
from notify import Level, send_telegram
from rms_guard import LiquidTopUpPlan, plan_liquid_topup


@dataclass
class LiquidFundingResult:
    as_of: date
    shortfall: float
    required_buffer: float
    emi_obligation: float
    plan: LiquidTopUpPlan
    intent: OrderIntent | None = None
    exec_result: ExecResult | None = None
    message: str = ""
    skipped_reason: str | None = None
    already_logged: bool = False
    exhausted: bool = False
    errors: list[str] | None = None


def required_cash_buffer(
    ledger: Ledger,
    cfg: dict,
    as_of: date,
) -> tuple[float, float]:
    """Return (required_buffer, emi_obligation)."""
    emi_obligation = ledger.remaining_emi_obligation(as_of)
    fire_shop = float(cfg.get("fire_shop_daily_reserve", 6000))
    return emi_obligation + fire_shop, emi_obligation


def evaluate_liquid_funding(
    ledger: Ledger,
    snapshot: AccountSnapshot,
    cfg: dict,
    as_of: date,
) -> tuple[float, float, LiquidTopUpPlan]:
    required, emi_obligation = required_cash_buffer(ledger, cfg, as_of)
    shortfall = max(0.0, required - snapshot.free_cash)
    plan = plan_liquid_topup(
        shortfall,
        snapshot.liquid_etf_value,
        min_reserve=float(cfg.get("liquid_etf_min_reserve", 10000)),
        max_sell_per_event=float(cfg.get("liquid_etf_max_sell_per_event", 25000)),
        cushion_pct=float(cfg.get("liquid_sell_cushion_pct", 0.02)),
        fire_shop_reserve=float(cfg.get("fire_shop_daily_reserve", 6000)),
    )
    return shortfall, required, plan


def build_liquid_sell_intent(
    snapshot: AccountSnapshot,
    holdings: list[dict],
    plan: LiquidTopUpPlan,
    cfg: dict,
    *,
    shortfall: float,
) -> OrderIntent | None:
    if plan.sell_amount <= 0:
        return None

    symbol = snapshot.liquid_etf_symbol
    qty, price = liquid_etf_position(holdings, symbol)
    if price <= 0:
        return None

    sell_qty = max(1, int(math.ceil(plan.sell_amount / price)))
    min_reserve = float(cfg.get("liquid_etf_min_reserve", 10000))
    reserve_qty = max(0, int(math.ceil(min_reserve / price)))
    max_sell_qty = max(0, qty - reserve_qty)
    if max_sell_qty <= 0:
        return None
    sell_qty = min(sell_qty, max_sell_qty)

    est_value = round(sell_qty * price, 2)
    return OrderIntent(
        side="sell",
        symbol=symbol,
        qty=sell_qty,
        product="CNC",
        reason=(
            f"LIQUIDCASE top-up shortfall ₹{shortfall:,.0f} "
            f"plan ₹{plan.sell_amount:,.0f} est ₹{est_value:,.0f}"
        ),
        limit_price=round(price * 0.999, 2),
    )


def format_liquid_intent_message(
    snapshot: AccountSnapshot,
    shortfall: float,
    plan: LiquidTopUpPlan,
    intent: OrderIntent | None,
) -> str:
    if plan.sell_amount <= 0:
        return f"No LIQUIDCASE sell needed (shortfall ₹{shortfall:,.0f})"
    if intent is None:
        return (
            f"DRY-RUN cannot size {snapshot.liquid_etf_symbol} sell "
            f"(shortfall ₹{shortfall:,.0f}, liquid ₹{snapshot.liquid_etf_value:,.0f})"
        )
    est = intent.qty * (intent.limit_price or 0)
    return (
        f"DRY-RUN sell {intent.symbol} qty={intent.qty} "
        f"~₹{est:,.0f} CNC (shortfall ₹{shortfall:,.0f})"
    )


def liquid_idempotency_key(as_of: date, symbol: str) -> str:
    return f"{as_of.isoformat()}|liquid|{symbol}"


def run_liquid_funding(
    ledger: Ledger,
    cfg: dict,
    *,
    kite: Any = None,
    holdings: list[dict] | None = None,
    equity_margins: dict | None = None,
    as_of: date | None = None,
    send_alerts: bool = True,
) -> LiquidFundingResult:
    as_of = as_of or date.today()
    mode = cfg.get("mode", "dry_run")
    errors: list[str] = []

    if kite is None and (holdings is None or equity_margins is None):
        return LiquidFundingResult(
            as_of=as_of,
            shortfall=0.0,
            required_buffer=0.0,
            emi_obligation=0.0,
            plan=plan_liquid_topup(
                0,
                0,
                min_reserve=float(cfg.get("liquid_etf_min_reserve", 10000)),
                max_sell_per_event=float(cfg.get("liquid_etf_max_sell_per_event", 25000)),
            ),
            errors=["kite or injected holdings/margins required"],
        )

    try:
        if holdings is None:
            holdings = kite.holdings()
        if equity_margins is None:
            equity_margins = kite.margins()["equity"]
        snapshot = build_account_snapshot(
            holdings,
            equity_margins,
            liquid_etf_symbol=cfg.get("liquid_etf_symbol", "LIQUIDCASE"),
        )
    except Exception as exc:
        msg = f"liquid_funding broker_read failed: {exc}"
        errors.append(msg)
        if send_alerts:
            send_telegram(msg, level=Level.ERROR)
        return LiquidFundingResult(
            as_of=as_of,
            shortfall=0.0,
            required_buffer=0.0,
            emi_obligation=0.0,
            plan=plan_liquid_topup(
                0,
                0,
                min_reserve=float(cfg.get("liquid_etf_min_reserve", 10000)),
                max_sell_per_event=float(cfg.get("liquid_etf_max_sell_per_event", 25000)),
            ),
            errors=errors,
        )

    shortfall, required, plan = evaluate_liquid_funding(ledger, snapshot, cfg, as_of)
    _, emi_obligation = required_cash_buffer(ledger, cfg, as_of)
    symbol = snapshot.liquid_etf_symbol
    idem = liquid_idempotency_key(as_of, symbol)

    if shortfall <= 0:
        msg = format_liquid_intent_message(snapshot, shortfall, plan, None)
        return LiquidFundingResult(
            as_of=as_of,
            shortfall=shortfall,
            required_buffer=required,
            emi_obligation=emi_obligation,
            plan=plan,
            message=msg,
            skipped_reason="no_shortfall",
        )

    min_reserve = float(cfg.get("liquid_etf_min_reserve", 10000))
    exhausted = (
        snapshot.liquid_etf_value <= min_reserve
        or plan.below_min_reserve
        and plan.sell_amount < shortfall
    )

    intent = build_liquid_sell_intent(snapshot, holdings, plan, cfg, shortfall=shortfall)
    msg = format_liquid_intent_message(snapshot, shortfall, plan, intent)

    if exhausted and send_alerts:
        send_telegram(
            f"LIQUIDCASE exhausted — deposit cash. Shortfall ₹{shortfall:,.0f}, "
            f"liquid ₹{snapshot.liquid_etf_value:,.0f} (min reserve ₹{min_reserve:,.0f})",
            level=Level.CRITICAL,
        )

    if intent is None:
        return LiquidFundingResult(
            as_of=as_of,
            shortfall=shortfall,
            required_buffer=required,
            emi_obligation=emi_obligation,
            plan=plan,
            message=msg,
            exhausted=exhausted,
            skipped_reason="cannot_size_intent",
        )

    from live_trading import execution_mode_for_liquid, liquid_live_enabled

    if ledger.has_order_intent(idem):
        return LiquidFundingResult(
            as_of=as_of,
            shortfall=shortfall,
            required_buffer=required,
            emi_obligation=emi_obligation,
            plan=plan,
            intent=intent,
            message=f"LIQUIDCASE intent already logged today ({symbol})",
            already_logged=True,
            exhausted=exhausted,
        )

    exec_mode = execution_mode_for_liquid(cfg)
    live_flag = liquid_live_enabled(cfg)
    try:
        exec_result = execute_intent(
            intent,
            mode=exec_mode,
            config_live_flag=live_flag,
            kite=kite,
            order_tag=cfg.get("live_order_tag"),
        )
    except Exception as exc:
        errors.append(str(exc))
        exec_result = None

    if exec_result:
        msg = exec_result.message
        row_id = ledger.log_order_intent(
            "sell",
            symbol,
            qty=intent.qty,
            product="CNC",
            mode=exec_mode,
            reason=intent.reason,
            idempotency_key=idem,
            broker_order_id=exec_result.broker_order_id,
        )
        if row_id and plan.sell_amount > 0:
            ledger.set_cash_reservation(as_of, "liquid_topup", plan.sell_amount)
    else:
        row_id = None

    level = Level.WARN
    if exhausted:
        level = Level.CRITICAL
    if send_alerts and exec_result and plan.sell_amount > 0:
        send_telegram(msg, level=level)

    return LiquidFundingResult(
        as_of=as_of,
        shortfall=shortfall,
        required_buffer=required,
        emi_obligation=emi_obligation,
        plan=plan,
        intent=intent,
        exec_result=exec_result,
        message=msg,
        exhausted=exhausted,
        errors=errors or None,
    )
