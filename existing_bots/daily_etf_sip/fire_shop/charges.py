#!/usr/bin/env python3
"""Zerodha CNC equity/ETF charges — Kite contract note + formula fallback."""

from __future__ import annotations

from typing import Any

# NSE equity delivery (Zerodha) — sell-side oriented fallback
# Brokerage line is ₹0 for CNC; statutory + DP still apply.
TC = {
    "stt_pct": 0.001,          # 0.1% sell
    "nse_txn_pct": 0.0000297,  # 0.00297%
    "sebi_pct": 0.000001,      # ₹10/crore
    "gst_rate": 0.18,
    "dp_flat": 15.34,
}


def formula_sell_charges(sell_value: float, *, dp_flat: float | None = None) -> dict[str, float]:
    """Estimate CNC sell-side charges when Kite API is unavailable."""
    sell_value = float(sell_value)
    brokerage = 0.0
    stt = TC["stt_pct"] * sell_value
    txn = TC["nse_txn_pct"] * sell_value
    sebi = TC["sebi_pct"] * sell_value
    gst = TC["gst_rate"] * (brokerage + txn)
    dp = float(dp_flat if dp_flat is not None else TC["dp_flat"])
    total = brokerage + stt + txn + sebi + gst + dp
    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "txn": round(txn, 2),
        "sebi": round(sebi, 4),
        "gst": round(gst, 4),
        "dp": round(dp, 2),
        "stamp": 0.0,
        "total": round(total, 2),
        "source": "formula",
    }


def _extract_charges_block(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, list) and payload:
        item = payload[0]
        if isinstance(item, dict):
            return item.get("charges") or item
    if isinstance(payload, dict):
        if "charges" in payload:
            return payload["charges"]
        data = payload.get("data")
        return _extract_charges_block(data)
    return None


def charges_from_kite_payload(
    payload: Any,
    *,
    sell_value: float,
    dp_flat: float = 15.34,
) -> dict[str, float]:
    """
    Normalize Kite /charges/orders response.
    Ensures DP is present (add flat fallback if missing/zero).
    """
    block = _extract_charges_block(payload)
    if not block:
        return formula_sell_charges(sell_value, dp_flat=dp_flat)

    total = float(block.get("total") or 0)
    dp = float(block.get("dp") or block.get("dp_charges") or 0)
    # Virtual contract note often omits DP — include fallback when absent.
    if dp <= 0:
        total = round(total + float(dp_flat), 2)
        dp = float(dp_flat)
        dp_source = "fallback"
    else:
        dp_source = "api"

    gst = block.get("gst") or {}
    gst_total = float(gst.get("total") or 0) if isinstance(gst, dict) else float(gst or 0)

    return {
        "brokerage": round(float(block.get("brokerage") or 0), 2),
        "stt": round(float(block.get("transaction_tax") or block.get("stt") or 0), 2),
        "txn": round(float(block.get("exchange_turnover_charge") or block.get("txn") or 0), 4),
        "sebi": round(float(block.get("sebi_turnover_charge") or block.get("sebi") or 0), 4),
        "gst": round(gst_total, 4),
        "dp": round(dp, 2),
        "stamp": round(float(block.get("stamp_duty") or 0), 2),
        "total": round(total, 2),
        "source": f"kite+dp_{dp_source}",
    }


def fetch_sell_charges(
    kite,
    *,
    order_id: str,
    tradingsymbol: str,
    quantity: int,
    average_price: float,
    dp_flat: float = 15.34,
) -> dict[str, float]:
    """Call Kite virtual contract note; fall back to formula on any error."""
    sell_value = float(quantity) * float(average_price)
    params = [
        {
            "order_id": str(order_id),
            "exchange": "NSE",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "variety": "regular",
            "product": "CNC",
            "order_type": "LIMIT",
            "quantity": int(quantity),
            "average_price": float(average_price),
        }
    ]
    try:
        raw = kite.get_virtual_contract_note(params)
        return charges_from_kite_payload(raw, sell_value=sell_value, dp_flat=dp_flat)
    except Exception:
        return formula_sell_charges(sell_value, dp_flat=dp_flat)
