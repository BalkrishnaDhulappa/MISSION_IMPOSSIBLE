"""EMI schedule math (Smart Margin sheet)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable


@dataclass(frozen=True)
class EmiInstallment:
    installment_no: int
    due_date: date
    amount: float


@dataclass(frozen=True)
class EmiSchedule:
    buy_value: float
    initial_margin: float
    buffer: float
    immediate_need: float
    broker_remaining: float
    weekly_emi: float
    installments: tuple[EmiInstallment, ...]


def compute_emi_schedule(
    buy_date: date,
    buy_value: float,
    initial_margin: float,
    *,
    buffer_pct: float = 0.10,
    emi_weeks: int = 16,
) -> EmiSchedule:
    """Build 10% buffer + 16 weekly EMI plan from fill inputs."""
    if buy_value <= 0:
        raise ValueError("buy_value must be positive")
    if initial_margin < 0 or initial_margin >= buy_value:
        raise ValueError("initial_margin must be in [0, buy_value)")

    buffer = round(buy_value * buffer_pct, 2)
    immediate_need = round(initial_margin + buffer, 2)
    broker_remaining = round(buy_value - immediate_need, 2)
    if broker_remaining <= 0:
        raise ValueError("broker_remaining must be positive after buffer + margin")

    weekly_emi = round(broker_remaining / emi_weeks, 2)
    installments: list[EmiInstallment] = []
    for i in range(1, emi_weeks + 1):
        installments.append(
            EmiInstallment(
                installment_no=i,
                due_date=buy_date + timedelta(days=7 * i),
                amount=weekly_emi,
            )
        )
    return EmiSchedule(
        buy_value=buy_value,
        initial_margin=initial_margin,
        buffer=buffer,
        immediate_need=immediate_need,
        broker_remaining=broker_remaining,
        weekly_emi=weekly_emi,
        installments=tuple(installments),
    )


@dataclass(frozen=True)
class OpenEmiRow:
    """One open position's unpaid EMI state."""

    position_id: int
    weekly_emi: float
    due_dates: tuple[date, ...]
    paid_flags: tuple[bool, ...]


def remaining_emi_obligation(
    rows: Iterable[OpenEmiRow],
    as_of: date,
) -> float:
    """Sum weekly_emi for each unpaid installment with due_date >= as_of."""
    total = 0.0
    for row in rows:
        for due, paid in zip(row.due_dates, row.paid_flags, strict=True):
            if not paid and due >= as_of:
                total += row.weekly_emi
    return round(total, 2)
