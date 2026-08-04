"""Market calendar helpers."""

from datetime import date

from calendar_ist import MarketCalendar, is_trading_day, should_run_scan


def test_is_trading_day_weekend():
    cal = MarketCalendar(frozenset(), 555, 930, "test")
    assert not is_trading_day(cal, date(2026, 8, 8))  # Saturday


def test_is_trading_day_holiday():
    cal = MarketCalendar(frozenset({date(2026, 8, 4)}), 555, 930, "test")
    assert not is_trading_day(cal, date(2026, 8, 4))


def test_should_run_scan_missing_calendar():
    assert should_run_scan({})
