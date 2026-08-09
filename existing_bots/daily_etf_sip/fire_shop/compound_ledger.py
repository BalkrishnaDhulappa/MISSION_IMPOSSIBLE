#!/usr/bin/env python3
"""FIRE ETF compound ledger — working capital + growing ticket."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_INITIAL_CAPITAL = 300_000.0
DEFAULT_PARTS = 50


def default_ledger(
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    parts: int = DEFAULT_PARTS,
) -> dict[str, Any]:
    ticket = round(initial_capital / parts, 2)
    return {
        "initial_capital": float(initial_capital),
        "working_capital": float(initial_capital),
        "parts": int(parts),
        "ticket": ticket,
        "total_growth": 0.0,
        "sells": [],
    }


def load_ledger(
    path: Path,
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    parts: int = DEFAULT_PARTS,
) -> dict[str, Any]:
    if not path.exists():
        ledger = default_ledger(initial_capital, parts)
        save_ledger(path, ledger)
        return ledger
    data = json.loads(path.read_text())
    # Ensure required keys
    base = default_ledger(
        float(data.get("initial_capital", initial_capital)),
        int(data.get("parts", parts)),
    )
    base.update(data)
    if "ticket" not in data or data.get("ticket") is None:
        base["ticket"] = round(float(base["working_capital"]) / int(base["parts"]), 2)
    if "sells" not in base or base["sells"] is None:
        base["sells"] = []
    return base


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.write_text(json.dumps(ledger, indent=2))


def current_ticket(ledger: dict[str, Any]) -> float:
    parts = int(ledger.get("parts") or DEFAULT_PARTS)
    wc = float(ledger.get("working_capital") or 0)
    return round(wc / parts, 2) if parts else 0.0


def compute_growth(sell_value: float, cost_basis: float, charges: float) -> float:
    return round(max(0.0, float(sell_value) - float(cost_basis) - float(charges)), 2)


def apply_growth(
    ledger: dict[str, Any],
    *,
    growth: float,
    sell_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add growth to WC, refresh ticket, optionally append sell history."""
    growth = round(max(0.0, float(growth)), 2)
    ledger["working_capital"] = round(float(ledger["working_capital"]) + growth, 2)
    ledger["total_growth"] = round(float(ledger.get("total_growth") or 0) + growth, 2)
    ledger["ticket"] = current_ticket(ledger)
    if sell_record is not None:
        record = dict(sell_record)
        record.setdefault("ts", datetime.now(IST).isoformat(timespec="seconds"))
        record["growth"] = growth
        record["wc_after"] = ledger["working_capital"]
        record["ticket_after"] = ledger["ticket"]
        ledger.setdefault("sells", []).append(record)
    return ledger
