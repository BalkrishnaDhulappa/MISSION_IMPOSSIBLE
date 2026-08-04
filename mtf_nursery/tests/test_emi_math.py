"""Unit tests for EMI math (Smart Margin sheet)."""

from datetime import date

from emi import OpenEmiRow, compute_emi_schedule, remaining_emi_obligation


def test_emi_schedule_15k_example():
    buy_date = date(2026, 1, 6)
    sched = compute_emi_schedule(
        buy_date,
        buy_value=15000,
        initial_margin=4500,
        buffer_pct=0.10,
        emi_weeks=16,
    )
    assert sched.buffer == 1500.0
    assert sched.immediate_need == 6000.0
    assert sched.broker_remaining == 9000.0
    assert sched.weekly_emi == 562.5
    assert len(sched.installments) == 16
    assert sched.installments[0].due_date == date(2026, 1, 13)
    assert sched.installments[-1].due_date == date(2026, 4, 28)


def test_remaining_emi_obligation_counts_unpaid_future():
    buy_date = date(2026, 1, 6)
    sched = compute_emi_schedule(buy_date, 15000, 4500)
    due_dates = tuple(i.due_date for i in sched.installments)
    paid = (False,) * 16
    row = OpenEmiRow(1, sched.weekly_emi, due_dates, paid)
    as_of = date(2026, 2, 1)
    future = sum(1 for d in due_dates if d >= as_of)
    assert remaining_emi_obligation([row], as_of) == round(sched.weekly_emi * future, 2)


def test_remaining_emi_obligation_excludes_paid():
    buy_date = date(2026, 1, 6)
    sched = compute_emi_schedule(buy_date, 15000, 4500)
    due_dates = tuple(i.due_date for i in sched.installments)
    paid = (True,) * 4 + (False,) * 12
    row = OpenEmiRow(1, sched.weekly_emi, due_dates, paid)
    as_of = date(2026, 1, 6)
    assert remaining_emi_obligation([row], as_of) == round(sched.weekly_emi * 12, 2)
