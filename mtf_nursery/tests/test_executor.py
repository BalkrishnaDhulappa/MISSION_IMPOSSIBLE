"""Tests for executor dry_run safety."""

import pytest

from executor import ExecMode, LiveTradingBlocked, OrderIntent, execute_intent


def test_dry_run_never_executes():
    intent = OrderIntent("buy", "RELIANCE", 10, "MTF", "test")
    result = execute_intent(intent, mode=ExecMode.DRY_RUN.value)
    assert not result.executed
    assert "DRY-RUN" in result.message


def test_live_blocked_without_confirm():
    intent = OrderIntent("buy", "RELIANCE", 10, "MTF", "test")
    with pytest.raises(LiveTradingBlocked):
        execute_intent(intent, mode=ExecMode.LIVE.value, config_live_flag=True)
