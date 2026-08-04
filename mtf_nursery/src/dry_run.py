"""Orchestrate real read-only dry_run cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from broker_read import AccountSnapshot, build_account_snapshot, funded_for_symbol
from emi_verify import EmiStatus
from executor import ExecMode, format_gate_block
from ledger import Ledger
from notify import Level, format_emi_alert, send_telegram
from rms_guard import (
    PositionRisk,
    RmsSeverity,
    account_cash_severity,
    max_severity,
    plan_liquid_topup,
    position_risk_severity,
)


@dataclass
class DryRunReport:
    as_of: date
    mode: str
    snapshot: AccountSnapshot | None = None
    emi_verified: list[str] = field(default_factory=list)
    emi_alerts_sent: list[str] = field(default_factory=list)
    rms_severity: str = "OK"
    rms_messages: list[str] = field(default_factory=list)
    liquid_topup_intent: str | None = None
    buy_gate_message: str | None = None
    errors: list[str] = field(default_factory=list)
    ledger_summary: dict | None = None


def run_dry_cycle(
    ledger: Ledger,
    cfg: dict,
    *,
    kite: Any = None,
    holdings: list[dict] | None = None,
    equity_margins: dict | None = None,
    as_of: date | None = None,
    send_alerts: bool = True,
) -> DryRunReport:
    as_of = as_of or date.today()
    mode = cfg.get("mode", ExecMode.DRY_RUN.value)
    report = DryRunReport(as_of=as_of, mode=mode)

    if kite is None and (holdings is None or equity_margins is None):
        report.errors.append("kite or injected holdings/margins required")
        return report

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
        report.snapshot = snapshot
    except Exception as exc:
        report.errors.append(f"broker_read failed: {exc}")
        if send_alerts:
            send_telegram(f"broker_read failed: {exc}", level=Level.ERROR)
        return report

    ledger.ensure_step(1, cfg.get("ticket_start", 15000))
    ledger.refresh_emi_statuses(as_of)
    report.ledger_summary = ledger.status_summary(as_of)

    tolerance = float(cfg.get("emi_verify_tolerance", 50.0))
    for emi in ledger.list_emis_needing_alert(as_of):
        funded = funded_for_symbol(snapshot, emi.symbol)
        if funded is not None:
            result = ledger.try_verify_emi_from_funded(
                emi.id, funded, tolerance=tolerance
            )
            if result.verified:
                msg = f"✅ EMI verified: {emi.symbol} #{emi.installment_no} ₹{emi.amount:.2f}"
                report.emi_verified.append(msg)
                if send_alerts:
                    send_telegram(msg, level=Level.INFO)
                continue

        level = Level.WARN if emi.status == EmiStatus.OVERDUE.value else Level.INFO
        msg = format_emi_alert(emi.symbol, emi.amount, emi.installment_no, emi.status)
        report.emi_alerts_sent.append(msg)
        if send_alerts:
            send_telegram(msg, level=level)

    _run_rms_checks(report, ledger, cfg, snapshot, as_of, send_alerts)
    _run_buy_gate_check(report, ledger, cfg, snapshot, as_of)

    if send_alerts and not report.errors:
        summary = report.ledger_summary or {}
        send_telegram(
            f"Dry-run OK {as_of.isoformat()} | cash ₹{snapshot.free_cash:,.0f} | "
            f"MTF positions {len(snapshot.mtf_holdings)} | "
            f"EMI obligation ₹{summary.get('remaining_emi_obligation', 0):,.0f}",
            level=Level.INFO,
        )
    return report


def _run_rms_checks(
    report: DryRunReport,
    ledger: Ledger,
    cfg: dict,
    snapshot: AccountSnapshot,
    as_of: date,
    send_alerts: bool,
) -> None:
    warn = float(cfg.get("rms_loss_warn_pct", 0.15))
    crit = float(cfg.get("rms_loss_critical_pct", 0.20))
    severities: list[RmsSeverity] = []

    emi_obligation = ledger.remaining_emi_obligation(as_of)
    required_buffer = emi_obligation + float(cfg.get("fire_shop_daily_reserve", 6000))
    severities.append(account_cash_severity(snapshot.free_cash, required_buffer))

    for h in snapshot.mtf_holdings:
        sev = position_risk_severity(
            PositionRisk(h.symbol, h.last_price * h.quantity, h.funded_estimate),
            warn_pct=warn,
            critical_pct=crit,
        )
        severities.append(sev)
        if sev != RmsSeverity.OK:
            report.rms_messages.append(f"{h.symbol} MTM risk {sev.value}")

    overall = max_severity(*severities) if severities else RmsSeverity.OK
    report.rms_severity = overall.value

    shortfall = max(0.0, required_buffer - snapshot.free_cash)
    if shortfall > 0:
        plan = plan_liquid_topup(
            shortfall,
            snapshot.liquid_etf_value,
            min_reserve=float(cfg.get("liquid_etf_min_reserve", 10000)),
            max_sell_per_event=float(cfg.get("liquid_etf_max_sell_per_event", 25000)),
            cushion_pct=float(cfg.get("liquid_sell_cushion_pct", 0.02)),
            fire_shop_reserve=float(cfg.get("fire_shop_daily_reserve", 6000)),
        )
        report.liquid_topup_intent = (
            f"DRY-RUN sell {snapshot.liquid_etf_symbol} ₹{plan.sell_amount:,.0f} "
            f"(shortfall ₹{shortfall:,.0f})"
        )
        if send_alerts and plan.sell_amount > 0:
            send_telegram(report.liquid_topup_intent, level=Level.WARN)

    if send_alerts and overall == RmsSeverity.CRITICAL:
        send_telegram(
            "RMS CRITICAL — review cash / MTF MTM. Buys blocked.",
            level=Level.CRITICAL,
        )


def _run_buy_gate_check(
    report: DryRunReport,
    ledger: Ledger,
    cfg: dict,
    snapshot: AccountSnapshot,
    as_of: date,
) -> None:
    ticket = ledger.current_ticket(cfg.get("ticket_start", 15000))
    # Estimate immediate need at ~40% margin + 10% buffer if no open ticket math
    est_margin = ticket * 0.30
    buffer = ticket * float(cfg.get("buffer_pct", 0.10))
    immediate = est_margin + buffer
    rms_block = report.rms_severity == RmsSeverity.CRITICAL.value
    gate = ledger.evaluate_buy(
        free_cash=snapshot.free_cash,
        ticket_immediate_need=immediate,
        as_of=as_of,
        fire_shop_reserve=float(cfg.get("fire_shop_daily_reserve", 6000)),
        max_buys_per_day=int(cfg.get("max_buys_per_day", 1)),
        max_mtf_buys_per_month=int(cfg.get("max_mtf_buys_per_month", 2)),
        buy_blocked_by_rms=rms_block,
    )
    if gate.allowed:
        report.buy_gate_message = (
            f"DRY-RUN would consider MTF buy up to ₹{ticket:,.0f} "
            f"(scanner C2 not wired — no symbol picked)"
        )
    else:
        report.buy_gate_message = format_gate_block("BUY", "?", gate)
