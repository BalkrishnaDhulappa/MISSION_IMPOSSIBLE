"""Tests for SQLite ledger (C1)."""

from datetime import date, timedelta

import pytest

from emi_verify import EmiStatus, PaidVia
from ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    db = tmp_path / "test.sqlite"
    lg = Ledger(db)
    lg.ensure_step(1, 15000.0)
    return lg


def test_add_position_creates_16_emis(ledger: Ledger):
    buy = date(2026, 1, 6)
    pos = ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    assert pos.buy_value == 15000.0
    assert pos.funded_baseline == 10500.0
    assert pos.weekly_emi == 562.5
    obligation = ledger.remaining_emi_obligation(buy)
    assert obligation == round(562.5 * 16, 2)


def test_remaining_obligation_decreases_after_verify(ledger: Ledger):
    buy = date(2026, 1, 6)
    ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    full = ledger.remaining_emi_obligation(buy)
    emis = ledger.list_emis_needing_alert(buy)
    assert len(emis) == 0
    ledger.refresh_emi_statuses(buy + timedelta(days=7))
    due = ledger.list_emis_needing_alert(buy + timedelta(days=7))
    assert len(due) == 1
    ledger.confirm_emi_manual(due[0].id)
    after = ledger.remaining_emi_obligation(buy + timedelta(days=7))
    assert after == round(full - 562.5, 2)


def test_verify_emi_from_funded_api(ledger: Ledger):
    buy = date(2026, 1, 6)
    pos = ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    due_date = buy + timedelta(days=7)
    ledger.refresh_emi_statuses(due_date)
    emi = ledger.list_emis_needing_alert(due_date)[0]
    funded_before = pos.funded_baseline
    result = ledger.try_verify_emi_from_funded(
        emi.id, funded_now=funded_before - 562.5, tolerance=10.0
    )
    assert result.verified
    pending = ledger.list_emis_needing_alert(due_date)
    assert len(pending) == 0


def test_verify_emi_fails_keeps_alerting(ledger: Ledger):
    buy = date(2026, 1, 6)
    pos = ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    due_date = buy + timedelta(days=7)
    ledger.refresh_emi_statuses(due_date)
    emi = ledger.list_emis_needing_alert(due_date)[0]
    result = ledger.try_verify_emi_from_funded(
        emi.id, funded_now=pos.funded_baseline, tolerance=10.0
    )
    assert not result.verified
    still = ledger.list_emis_needing_alert(due_date)
    assert len(still) == 1
    assert still[0].status == EmiStatus.PENDING_REPAY.value


def test_buy_gate_blocks_when_obligation_high(ledger: Ledger):
    buy = date(2026, 1, 6)
    pos = ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    sched = pos.buffer_10pct + pos.initial_margin
    result = ledger.evaluate_buy(
        free_cash=20000,
        ticket_immediate_need=sched,
        as_of=buy,
        fire_shop_reserve=6000,
    )
    assert not result.allowed
    assert "insufficient_cash_after_obligation" in result.reasons


def test_buy_gate_allows_when_cash_sufficient(ledger: Ledger):
    buy = date(2026, 1, 6)
    ledger.add_position("RELIANCE", buy, qty=10, avg_price=1500, initial_margin=4500)
    result = ledger.evaluate_buy(
        free_cash=100000,
        ticket_immediate_need=6000,
        as_of=buy,
        fire_shop_reserve=6000,
    )
    assert result.allowed


def test_order_idempotency(ledger: Ledger):
    today = date.today()
    key = f"{today}|buy|RELIANCE"
    first = ledger.log_order_intent("buy", "RELIANCE", idempotency_key=key)
    second = ledger.log_order_intent("buy", "RELIANCE", idempotency_key=key)
    assert first is not None
    assert second is None
    assert ledger.count_buys_on(today) == 1


def test_overdue_emi_status(ledger: Ledger):
    buy = date(2026, 1, 1)
    ledger.add_position("TCS", buy, qty=5, avg_price=3000, initial_margin=4500)
    as_of = buy + timedelta(days=14)
    ledger.refresh_emi_statuses(as_of)
    alerts = ledger.list_emis_needing_alert(as_of)
    assert any(e.status == EmiStatus.OVERDUE.value for e in alerts)


def test_status_summary(ledger: Ledger):
    ledger.add_position("INFY", date(2026, 2, 1), qty=10, avg_price=1500, initial_margin=4500)
    summary = ledger.status_summary(date(2026, 2, 1))
    assert summary["open_positions"] == 1
    assert summary["current_ticket"] == 15000.0
