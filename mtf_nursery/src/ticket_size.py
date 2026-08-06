"""Ticket notional sizing — always ≥ ticket for the chosen (top scan) symbol."""

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


def qty_for_ticket(ticket: float, cmp: float) -> TicketFill:
    """
    Shares for this symbol so notional is always ≥ ticket.

    - If 1 share ≥ ticket (e.g. ₹20k stock): buy 1.
    - Else: ceil(ticket / cmp) so we never undershoot.
    """
    if ticket <= 0:
        raise ValueError("ticket must be positive")
    if cmp <= 0:
        raise ValueError("cmp must be positive")

    if cmp >= ticket:
        qty = 1
    else:
        qty = max(1, int(math.ceil(ticket / cmp)))

    notional = round(qty * cmp, 2)
    return TicketFill(
        qty=qty,
        cmp=cmp,
        notional=notional,
        ticket=ticket,
        undershoot=notional < ticket,
    )
