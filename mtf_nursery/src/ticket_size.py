"""Ticket notional sizing — ≥ ticket, never above max budget per buy."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketFill:
    qty: int
    cmp: float
    notional: float
    ticket: float
    undershoot: bool
    skip_reason: str | None = None


def qty_for_ticket(
    ticket: float,
    cmp: float,
    *,
    max_notional: float = 30000.0,
) -> TicketFill | None:
    """
    Size shares for this symbol.

    Rules:
    - Notional must be ≥ ticket (ceil), never below.
    - If 1 share ≥ ticket and ≤ max_notional: buy 1 (e.g. ₹20k stock OK).
    - If 1 share > max_notional: skip (too expensive).
    - If ceil(ticket/cmp) notional > max_notional: skip (cannot meet ticket in budget).
    """
    if ticket <= 0:
        raise ValueError("ticket must be positive")
    if cmp <= 0:
        raise ValueError("cmp must be positive")
    if max_notional < ticket:
        raise ValueError("max_notional must be >= ticket")

    if cmp > max_notional:
        return None

    if cmp >= ticket:
        return TicketFill(
            qty=1,
            cmp=cmp,
            notional=round(cmp, 2),
            ticket=ticket,
            undershoot=False,
        )

    qty = max(1, int(math.ceil(ticket / cmp)))
    notional = round(qty * cmp, 2)
    if notional > max_notional:
        return None

    return TicketFill(
        qty=qty,
        cmp=cmp,
        notional=notional,
        ticket=ticket,
        undershoot=False,
    )


def pick_top_affordable(
    candidates: list[dict],
    ticket: float,
    *,
    max_notional: float = 30000.0,
) -> tuple[dict, TicketFill] | None:
    """First D1=A candidate (already ranked by dist_200) that fits ticket rules."""
    for c in candidates:
        cmp = float(c.get("cmp") or 0)
        if cmp <= 0:
            continue
        fill = qty_for_ticket(ticket, cmp, max_notional=max_notional)
        if fill is not None:
            return c, fill
    return None
