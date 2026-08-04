"""Read Zerodha portfolio state for dry_run (no orders)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from emi_verify import estimate_funded_from_mtf_block


@dataclass(frozen=True)
class MtfHoldingView:
    symbol: str
    exchange: str
    quantity: int
    average_price: float
    mtf_value: float
    initial_margin: float
    funded_estimate: float
    last_price: float


@dataclass(frozen=True)
class AccountSnapshot:
    free_cash: float
    available_cash: float
    mtf_holdings: tuple[MtfHoldingView, ...]
    liquid_etf_value: float
    liquid_etf_symbol: str


def parse_mtf_holding(row: dict) -> MtfHoldingView | None:
    mtf = row.get("mtf") or {}
    qty = int(mtf.get("quantity") or 0)
    if qty <= 0:
        return None
    mtf_value = float(mtf.get("value") or 0)
    initial_margin = float(mtf.get("initial_margin") or 0)
    return MtfHoldingView(
        symbol=row["tradingsymbol"],
        exchange=row.get("exchange", "NSE"),
        quantity=qty,
        average_price=float(mtf.get("average_price") or 0),
        mtf_value=mtf_value,
        initial_margin=initial_margin,
        funded_estimate=estimate_funded_from_mtf_block(mtf_value, initial_margin),
        last_price=float(row.get("last_price") or 0),
    )


def parse_holdings(holdings: list[dict]) -> list[MtfHoldingView]:
    out: list[MtfHoldingView] = []
    for row in holdings:
        view = parse_mtf_holding(row)
        if view:
            out.append(view)
    return out


def free_cash_from_margins(equity_margins: dict) -> tuple[float, float]:
    """Return (available cash for trading, net cash)."""
    avail = equity_margins.get("available") or {}
    available_cash = float(avail.get("cash") or 0)
    net = float(avail.get("live_balance") or avail.get("opening_balance") or available_cash)
    return available_cash, net


def liquid_etf_value(holdings: list[dict], symbol: str) -> float:
    total = 0.0
    for row in holdings:
        if row.get("tradingsymbol") != symbol:
            continue
        qty = float(row.get("quantity") or 0) + float(row.get("t1_quantity") or 0)
        price = float(row.get("last_price") or row.get("close_price") or 0)
        total += qty * price
    return round(total, 2)


def build_account_snapshot(
    holdings: list[dict],
    equity_margins: dict,
    *,
    liquid_etf_symbol: str = "LIQUIDCASE",
) -> AccountSnapshot:
    cash, net = free_cash_from_margins(equity_margins)
    mtf = parse_holdings(holdings)
    liq = liquid_etf_value(holdings, liquid_etf_symbol)
    return AccountSnapshot(
        free_cash=net,
        available_cash=cash,
        mtf_holdings=tuple(mtf),
        liquid_etf_value=liq,
        liquid_etf_symbol=liquid_etf_symbol,
    )


def funded_for_symbol(snapshot: AccountSnapshot, symbol: str) -> float | None:
    for h in snapshot.mtf_holdings:
        if h.symbol == symbol:
            return h.funded_estimate
    return None
