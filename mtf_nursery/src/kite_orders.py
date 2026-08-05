"""Kite Connect order placement helpers (C6)."""

from __future__ import annotations

from typing import Any

from order_types import OrderIntent


class KiteOrderError(RuntimeError):
    pass


def _exchange_constant(kite: Any, exchange: str) -> str:
    key = f"EXCHANGE_{exchange.upper()}"
    return getattr(kite, key, exchange.upper())


def _product_value(kite: Any, product: str) -> str:
    product = product.upper()
    if product == "MTF":
        return "MTF"
    key = f"PRODUCT_{product}"
    return getattr(kite, key, product)


def place_kite_order(kite: Any, intent: OrderIntent, *, tag: str | None = None) -> str:
    """Place a regular LIMIT/MARKET order; return broker order id."""
    tx = intent.side.lower()
    if tx == "buy":
        transaction_type = kite.TRANSACTION_TYPE_BUY
    elif tx == "sell":
        transaction_type = kite.TRANSACTION_TYPE_SELL
    else:
        raise KiteOrderError(f"Unsupported side: {intent.side}")

    product = _product_value(kite, intent.product)
    exchange = _exchange_constant(kite, intent.exchange)

    if intent.limit_price is None:
        order_type = kite.ORDER_TYPE_MARKET
        price = None
    else:
        order_type = kite.ORDER_TYPE_LIMIT
        price = float(intent.limit_price)

    params: dict[str, Any] = {
        "variety": kite.VARIETY_REGULAR,
        "exchange": exchange,
        "tradingsymbol": intent.symbol,
        "transaction_type": transaction_type,
        "quantity": int(intent.qty),
        "product": product,
        "order_type": order_type,
        "validity": kite.VALIDITY_DAY,
    }
    if price is not None:
        params["price"] = price
    if tag:
        params["tag"] = tag[:20]

    try:
        order_id = kite.place_order(**params)
    except Exception as exc:
        raise KiteOrderError(str(exc)) from exc
    return str(order_id)
