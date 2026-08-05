"""Ticket notional sizing — prefer ~ticket, at or above when reasonable."""

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


def qty_for_ticket(
    ticket: float,
    cmp: float,
    *,
    prefer_above: bool = True,
    max_overshoot_pct: float = 0.25,
) -> TicketFill:
    """
    Choose share qty so notional is close to ticket.

    prefer_above: if floor undershoots, use ceil when overshoot ≤ max_overshoot_pct.
    Otherwise pick whichever of floor/ceil is closer (ties go up).
    If one share already ≥ ticket, buy 1.
    """
    if ticket <= 0:
        raise ValueError("ticket must be positive")
    if cmp <= 0:
        raise ValueError("cmp must be positive")

    if cmp >= ticket:
        return TicketFill(1, cmp, round(cmp, 2), ticket, undershoot=False)

    flo = max(1, int(math.floor(ticket / cmp)))
    cei = max(flo, int(math.ceil(ticket / cmp)))
    flo_val = flo * cmp
    cei_val = cei * cmp

    if prefer_above and flo_val < ticket:
        overshoot = (cei_val - ticket) / ticket
        if overshoot <= max_overshoot_pct:
            qty = cei
        else:
            # Ceil too expensive (e.g. ULTRACEMCO 2×) — stay at floor
            qty = flo
    else:
        # Closer of floor/ceil; ties prefer higher
        if abs(flo_val - ticket) < abs(cei_val - ticket):
            qty = flo
        else:
            qty = cei

    notional = round(qty * cmp, 2)
    return TicketFill(
        qty=qty,
        cmp=cmp,
        notional=notional,
        ticket=ticket,
        undershoot=notional < ticket,
    )


def pick_best_ticket_fill(
    candidates: list[dict],
    ticket: float,
    *,
    prefer_above: bool = True,
    max_overshoot_pct: float = 0.25,
    among_top: int | None = None,
) -> tuple[dict, TicketFill] | None:
    """
    Among scan candidates (already ranked), pick the fill closest to ticket,
    preferring notional ≥ ticket.
    """
    if not candidates:
        return None
    pool = candidates if among_top is None else candidates[:among_top]
    best: tuple[dict, TicketFill] | None = None
    best_key: tuple | None = None
    for c in pool:
        cmp = float(c.get("cmp") or 0)
        if cmp <= 0:
            continue
        fill = qty_for_ticket(
            ticket,
            cmp,
            prefer_above=prefer_above,
            max_overshoot_pct=max_overshoot_pct,
        )
        # Prefer at/above ticket, then closest absolute distance
        key = (1 if fill.undershoot else 0, abs(fill.notional - ticket))
        if best_key is None or key < best_key:
            best = (c, fill)
            best_key = key
    return best
