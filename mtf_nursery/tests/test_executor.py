"""Tests for live executor and Kite order placement."""

import os
from unittest.mock import MagicMock

import pytest

from executor import ExecMode, LiveTradingBlocked, OrderIntent, execute_intent
from kite_orders import place_kite_order


class _MockKite:
    VARIETY_REGULAR = "regular"
    EXCHANGE_NSE = "NSE"
    PRODUCT_CNC = "CNC"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_MARKET = "MARKET"
    VALIDITY_DAY = "DAY"

    def __init__(self):
        self.last_order = None

    def place_order(self, **kwargs):
        self.last_order = kwargs
        return "ORD123"


def test_live_places_mtf_order_with_confirm(monkeypatch):
    monkeypatch.setenv("LIVE_CONFIRM", "YES")
    kite = _MockKite()
    intent = OrderIntent(
        "buy", "RELIANCE", 10, "MTF", "test", limit_price=2500.0
    )
    result = execute_intent(
        intent,
        mode=ExecMode.LIVE.value,
        config_live_flag=True,
        kite=kite,
        order_tag="mtf_nursery",
    )
    assert result.executed
    assert result.broker_order_id == "ORD123"
    assert kite.last_order["product"] == "MTF"
    assert kite.last_order["quantity"] == 10


def test_live_blocked_without_config_flag(monkeypatch):
    monkeypatch.setenv("LIVE_CONFIRM", "YES")
    intent = OrderIntent("buy", "RELIANCE", 10, "MTF", "test")
    with pytest.raises(LiveTradingBlocked):
        execute_intent(
            intent,
            mode=ExecMode.LIVE.value,
            config_live_flag=False,
            kite=_MockKite(),
        )


def test_place_kite_order_mtf_product_string():
    kite = _MockKite()
    intent = OrderIntent("sell", "INFY", 5, "MTF", "exit", limit_price=1500)
    order_id = place_kite_order(kite, intent)
    assert order_id == "ORD123"
    assert kite.last_order["product"] == "MTF"


def test_live_blocked_without_confirm():
    intent = OrderIntent("buy", "RELIANCE", 10, "MTF", "test")
    with pytest.raises(LiveTradingBlocked):
        execute_intent(
            intent,
            mode=ExecMode.LIVE.value,
            config_live_flag=True,
            kite=_MockKite(),
        )
