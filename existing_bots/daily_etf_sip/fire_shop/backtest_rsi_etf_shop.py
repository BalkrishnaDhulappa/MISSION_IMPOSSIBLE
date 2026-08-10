#!/usr/bin/env python3
"""
Backtest: ETF shop — RSI vs DMA, with BID modes + parameter sweep
================================================================
Default single-run params:
  - Universe: etf_universe.json
  - Entry: 1 buy/day
  - Rank: RSI(14) lowest  |  DMA deepest dip vs 20DMA
  - Size: WC ₹3L / 50 parts; compounds after sells
  - Exit: LTP >= avg × (1+gate), 1 sell/day, full lot, best unrealized %
  - BID (when enabled): size max(invested/2, ticket), vs last_buy
  - Day order (T1): BUY first, then SELL (growth → next-day ticket)
  - Charges: CNC sell-side formula; no tax set-aside

BID modes:
  off        — NEW only (not already held)
  compete    — NEW + BID-eligible held share one rank list (shop-style)
  new_first  — NEW if any affordable; else BID (live engine order, always-fill)
  bid_first  — BID if any eligible/affordable; else NEW

Usage:
  python3 backtest_rsi_etf_shop.py --start 2024-09-01
  python3 backtest_rsi_etf_shop.py --sweep --start 2024-09-01
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from charges import formula_sell_charges  # noqa: E402

UNIVERSE_FILE = BASE / "etf_universe.json"
OUT_DIR = BASE / "results"
CACHE_DIR = BASE / "data_cache"
PANEL_CACHE = CACHE_DIR / "etf_panel_shop.pkl"


@dataclass
class Position:
    code: str
    qty: int
    avg: float
    cost: float
    last_buy: float
    invested: float
    bid_count: int = 0


@dataclass
class Ledger:
    working_capital: float
    parts: int = 50
    total_growth: float = 0.0

    @property
    def ticket(self) -> float:
        return round(self.working_capital / self.parts, 2)


def yahoo_symbol(nse_code: str) -> str:
    return nse_code.replace("NSE:", "") + ".NS"


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def load_universe() -> list[str]:
    data = json.loads(UNIVERSE_FILE.read_text())
    return sorted(data.keys())


def download_panel(codes: list[str], start: str, end: str | None) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    panel: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes, 1):
        ysym = yahoo_symbol(code)
        print(f"  [{i}/{len(codes)}] {ysym}...", flush=True)
        try:
            df = yf.download(
                ysym,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
                threads=False,
            )
        except Exception as e:
            print(f"    skip download error: {e}")
            continue
        if df is None or df.empty:
            print("    skip empty")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        if "close" not in df.columns:
            print("    skip no close")
            continue
        close = df["close"].astype(float)
        out = pd.DataFrame(
            {
                "close": close,
                "rsi": rsi_series(close, 14),
                "dma20": close.rolling(20).mean(),
            },
            index=pd.to_datetime(df.index).tz_localize(None),
        )
        out["dip"] = (out["close"] - out["dma20"]) / out["dma20"]
        out = out.dropna(subset=["rsi", "dma20"])
        if len(out) < 40:
            print(f"    skip short history ({len(out)})")
            continue
        panel[code] = out
        time.sleep(0.12)
    return panel


def load_or_download_panel(
    codes: list[str], data_start: str, end: str | None, *, refresh: bool = False
) -> dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(exist_ok=True)
    meta_key = {"data_start": data_start, "end": end, "n": len(codes)}
    if PANEL_CACHE.exists() and not refresh:
        try:
            cached = pickle.loads(PANEL_CACHE.read_bytes())
            if cached.get("meta") == meta_key and set(codes).issubset(cached["panel"]):
                print(f"Using cached panel ({len(cached['panel'])} series)")
                return {c: cached["panel"][c] for c in codes if c in cached["panel"]}
        except Exception as e:
            print(f"Cache unusable ({e}) — re-downloading")
    print(f"Downloading from {data_start} ...")
    panel = download_panel(codes, data_start, end)
    PANEL_CACHE.write_bytes(pickle.dumps({"meta": meta_key, "panel": panel}))
    print(f"Cached {len(panel)} series → {PANEL_CACHE}")
    return panel


def trading_days(panel: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp):
    idx = None
    for df in panel.values():
        idx = df.index if idx is None else idx.union(df.index)
    if idx is None:
        return []
    idx = idx.sort_values()
    return [d for d in idx if start <= d <= end]


def snapshot(panel, day: pd.Timestamp) -> dict[str, dict]:
    snap = {}
    for code, df in panel.items():
        if day not in df.index:
            continue
        row = df.loc[day]
        px = float(row["close"])
        if px <= 0 or math.isnan(px):
            continue
        snap[code] = {
            "close": px,
            "rsi": float(row["rsi"]),
            "dip": float(row["dip"]),
        }
    return snap


def _rank_key(mode: str, v: dict):
    return v["rsi"] if mode == "rsi" else v["dip"]


def pick_sell(positions: dict[str, Position], snap: dict, gate: float) -> str | None:
    best = None
    best_pct = -1e9
    for code, pos in positions.items():
        if code not in snap or pos.avg <= 0:
            continue
        px = snap[code]["close"]
        target = round(pos.avg * (1.0 + gate), 2)
        if px < target:
            continue
        pct = px / pos.avg - 1.0
        if pct > best_pct:
            best_pct = pct
            best = code
    return best


def bid_eligible(pos: Position, px: float, bid_threshold: float, max_bid: int) -> bool:
    if pos.bid_count >= max_bid:
        return False
    return px <= pos.last_buy * (1.0 - bid_threshold)


def bid_amount(pos: Position, ticket: float) -> float:
    return max(float(pos.invested) / 2.0, ticket)


def pick_buy(
    mode: str,
    snap: dict,
    positions: dict[str, Position],
    cash: float,
    ticket: float,
    *,
    bid_mode: str,
    bid_threshold: float,
    max_bid: int,
) -> tuple[str | None, str, int, float]:
    """Return (code, type, qty, spend) or (None, '', 0, 0)."""

    def new_candidates():
        out = []
        for c, v in snap.items():
            if c in positions:
                continue
            px = v["close"]
            qty = int(math.floor(ticket / px))
            if qty < 1:
                continue
            spend = qty * px
            if spend > cash:
                continue
            out.append((c, "NEW", qty, spend, _rank_key(mode, v)))
        return out

    def bid_candidates():
        out = []
        for c, pos in positions.items():
            if c not in snap:
                continue
            px = snap[c]["close"]
            if not bid_eligible(pos, px, bid_threshold, max_bid):
                continue
            amt = bid_amount(pos, ticket)
            qty = int(math.floor(amt / px))
            if qty < 1:
                continue
            spend = qty * px
            if spend > cash:
                # try ticket-sized bid if half-invested is too large
                qty2 = int(math.floor(ticket / px))
                spend2 = qty2 * px
                if qty2 < 1 or spend2 > cash:
                    continue
                qty, spend = qty2, spend2
            out.append((c, "BID", qty, spend, _rank_key(mode, snap[c])))
        return out

    news = new_candidates()
    bids = bid_candidates() if bid_mode != "off" else []

    chosen = None
    if bid_mode == "off":
        pool = news
    elif bid_mode == "compete":
        pool = news + bids
    elif bid_mode == "new_first":
        pool = news if news else bids
    elif bid_mode == "bid_first":
        pool = bids if bids else news
    else:
        raise ValueError(f"unknown bid_mode: {bid_mode}")

    if not pool:
        return None, "", 0, 0.0

    # lower RSI / deeper (more negative) dip wins
    pool.sort(key=lambda x: x[4])
    code, typ, qty, spend, _ = pool[0]
    return code, typ, qty, spend


def run_strategy(
    mode: str,
    panel: dict[str, pd.DataFrame],
    days: list[pd.Timestamp],
    *,
    initial_capital: float,
    parts: int,
    gate: float,
    sell_buffer: float,
    bid_mode: str = "off",
    bid_threshold: float = 0.04,
    max_bid: int = 3,
) -> dict:
    cash = float(initial_capital)
    ledger = Ledger(working_capital=float(initial_capital), parts=parts)
    positions: dict[str, Position] = {}
    buys = []
    sells = []
    equity_curve = []
    bid_buys = 0
    new_buys = 0

    for day in days:
        snap = snapshot(panel, day)
        ticket = ledger.ticket

        # 1) BUY first
        code, typ, qty, spend = pick_buy(
            mode,
            snap,
            positions,
            cash,
            ticket,
            bid_mode=bid_mode,
            bid_threshold=bid_threshold,
            max_bid=max_bid,
        )
        if code is not None and qty >= 1:
            px = snap[code]["close"]
            cash -= spend
            if typ == "NEW":
                positions[code] = Position(
                    code=code,
                    qty=qty,
                    avg=px,
                    cost=spend,
                    last_buy=px,
                    invested=spend,
                    bid_count=0,
                )
                new_buys += 1
            else:
                pos = positions[code]
                new_qty = pos.qty + qty
                new_cost = pos.cost + spend
                pos.qty = new_qty
                pos.cost = new_cost
                pos.avg = new_cost / new_qty
                pos.last_buy = px
                pos.invested = pos.invested + spend
                pos.bid_count += 1
                bid_buys += 1
            buys.append(
                {
                    "date": day.date().isoformat(),
                    "code": code,
                    "type": typ,
                    "qty": qty,
                    "price": round(px, 4),
                    "cost": round(spend, 2),
                    "ticket": ticket,
                    "rsi": round(snap[code]["rsi"], 2),
                    "dip": round(snap[code]["dip"], 4),
                    "bid_count_after": positions[code].bid_count,
                }
            )

        # 2) SELL (growth → next day ticket)
        sell_code = pick_sell(positions, snap, gate)
        if sell_code is not None:
            pos = positions[sell_code]
            ltp = snap[sell_code]["close"]
            fill = round(ltp * (1.0 - sell_buffer), 4)
            sell_value = pos.qty * fill
            charges = formula_sell_charges(sell_value)["total"]
            growth = round(max(0.0, sell_value - pos.cost - charges), 2)
            cash += sell_value
            cash -= charges
            ledger.working_capital = round(ledger.working_capital + growth, 2)
            ledger.total_growth = round(ledger.total_growth + growth, 2)
            sells.append(
                {
                    "date": day.date().isoformat(),
                    "code": sell_code,
                    "qty": pos.qty,
                    "avg": round(pos.avg, 4),
                    "fill": fill,
                    "sell_value": round(sell_value, 2),
                    "charges": charges,
                    "growth": growth,
                    "pnl_pct": round(fill / pos.avg - 1.0, 4),
                    "ticket_after": ledger.ticket,
                }
            )
            del positions[sell_code]

        mtm = cash + sum(
            pos.qty * snap[c]["close"] for c, pos in positions.items() if c in snap
        )
        equity_curve.append({"date": day.date().isoformat(), "equity": round(mtm, 2)})

    final_snap = snapshot(panel, days[-1]) if days else {}
    holdings_val = sum(
        pos.qty * final_snap[c]["close"]
        for c, pos in positions.items()
        if c in final_snap
    )
    final_equity = round(cash + holdings_val, 2)
    years = max((days[-1] - days[0]).days / 365.25, 1e-9) if len(days) > 1 else 1.0
    total_return = final_equity / initial_capital - 1.0
    cagr = (
        (final_equity / initial_capital) ** (1 / years) - 1.0 if final_equity > 0 else -1.0
    )

    eq = pd.Series([e["equity"] for e in equity_curve])
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    return {
        "mode": mode,
        "bid_mode": bid_mode,
        "bid_threshold": bid_threshold,
        "max_bid": max_bid,
        "gate": gate,
        "parts": parts,
        "start": days[0].date().isoformat() if days else None,
        "end": days[-1].date().isoformat() if days else None,
        "trading_days": len(days),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "cash": round(cash, 2),
        "holdings_value": round(holdings_val, 2),
        "open_positions": len(positions),
        "buys": len(buys),
        "new_buys": new_buys,
        "bid_buys": bid_buys,
        "sells": len(sells),
        "total_growth": ledger.total_growth,
        "final_ticket": ledger.ticket,
        "final_wc": ledger.working_capital,
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_sell_pnl_pct": round(
            float(np.mean([s["pnl_pct"] for s in sells]) * 100) if sells else 0.0, 2
        ),
        "buy_log": buys,
        "sell_log": sells,
        "equity_curve": equity_curve,
        "open_lots": {
            c: {
                "qty": p.qty,
                "avg": p.avg,
                "cost": p.cost,
                "bid_count": p.bid_count,
            }
            for c, p in positions.items()
        },
    }


def summary_row(res: dict) -> dict:
    return {
        "rank": res["mode"],
        "bid_mode": res["bid_mode"],
        "bid_threshold": res["bid_threshold"],
        "max_bid": res["max_bid"],
        "gate": res["gate"],
        "parts": res["parts"],
        "final_equity": res["final_equity"],
        "total_return_pct": res["total_return_pct"],
        "cagr_pct": res["cagr_pct"],
        "max_drawdown_pct": res["max_drawdown_pct"],
        "buys": res["buys"],
        "new_buys": res["new_buys"],
        "bid_buys": res["bid_buys"],
        "sells": res["sells"],
        "total_growth": res["total_growth"],
        "final_ticket": res["final_ticket"],
        "open_positions": res["open_positions"],
        "avg_sell_pnl_pct": res["avg_sell_pnl_pct"],
    }


def run_sweep(
    panel: dict[str, pd.DataFrame],
    days: list[pd.Timestamp],
    *,
    capital: float,
    sell_buffer: float,
) -> pd.DataFrame:
    """Focused grids: gate×bid, bid-params, parts."""
    rows = []

    gates = [0.0471, 0.05, 0.0628, 0.0638, 0.07, 0.08]
    ranks = ["rsi", "dma"]
    bid_modes = ["off", "compete", "new_first", "bid_first"]

    # A) gate × bid_mode (live-like BID knobs)
    print("\n=== Sweep A: gate × bid_mode ===")
    for gate, mode, bid_mode in itertools.product(gates, ranks, bid_modes):
        res = run_strategy(
            mode,
            panel,
            days,
            initial_capital=capital,
            parts=50,
            gate=gate,
            sell_buffer=sell_buffer,
            bid_mode=bid_mode,
            bid_threshold=0.04,
            max_bid=3,
        )
        row = summary_row(res)
        row["sweep"] = "A_gate_bidmode"
        rows.append(row)
        print(
            f"  {mode}/{bid_mode}/gate={gate:.4f} → "
            f"{row['total_return_pct']}%  bids={row['bid_buys']}",
            flush=True,
        )

    # B) bid threshold × max_bid (compete + live gate)
    print("\n=== Sweep B: bid threshold × max_bid ===")
    for thresh, mb, mode in itertools.product(
        [0.025, 0.03, 0.04, 0.05], [2, 3, 4], ranks
    ):
        res = run_strategy(
            mode,
            panel,
            days,
            initial_capital=capital,
            parts=50,
            gate=0.0638,
            sell_buffer=sell_buffer,
            bid_mode="compete",
            bid_threshold=thresh,
            max_bid=mb,
        )
        row = summary_row(res)
        row["sweep"] = "B_bid_params"
        rows.append(row)
        print(
            f"  {mode}/compete/th={thresh}/max={mb} → "
            f"{row['total_return_pct']}%  bids={row['bid_buys']}",
            flush=True,
        )

    # C) parts × bid on/off
    print("\n=== Sweep C: parts × bid on/off ===")
    for parts, mode, bid_mode in itertools.product(
        [30, 40, 50, 60], ranks, ["off", "compete"]
    ):
        res = run_strategy(
            mode,
            panel,
            days,
            initial_capital=capital,
            parts=parts,
            gate=0.0638,
            sell_buffer=sell_buffer,
            bid_mode=bid_mode,
            bid_threshold=0.04,
            max_bid=3,
        )
        row = summary_row(res)
        row["sweep"] = "C_parts"
        rows.append(row)
        print(
            f"  {mode}/{bid_mode}/parts={parts} → "
            f"{row['total_return_pct']}%  ticket≈{row['final_ticket']}",
            flush=True,
        )

    return pd.DataFrame(rows)


def print_top(df: pd.DataFrame, n: int = 15):
    print("\n" + "=" * 72)
    print(f"TOP {n} BY TOTAL RETURN")
    print("=" * 72)
    cols = [
        "sweep",
        "rank",
        "bid_mode",
        "bid_threshold",
        "max_bid",
        "gate",
        "parts",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "bid_buys",
        "sells",
        "final_equity",
    ]
    top = df.sort_values("total_return_pct", ascending=False).head(n)
    print(top[cols].to_string(index=False))

    print("\n" + "=" * 72)
    print("BEST PER BID MODE (across all sweeps)")
    print("=" * 72)
    for bm, g in df.groupby("bid_mode"):
        best = g.sort_values("total_return_pct", ascending=False).iloc[0]
        print(
            f"  {bm:10s}  ret={best['total_return_pct']:6.2f}%  "
            f"cagr={best['cagr_pct']:5.2f}%  dd={best['max_drawdown_pct']:6.2f}%  "
            f"rank={best['rank']} gate={best['gate']} parts={best['parts']} "
            f"th={best['bid_threshold']} maxbid={best['max_bid']} "
            f"bids={int(best['bid_buys'])}"
        )


def main():
    ap = argparse.ArgumentParser(description="RSI vs DMA ETF shop backtest + sweep")
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--data-start", default="2023-01-01", help="download warmup start")
    ap.add_argument("--capital", type=float, default=300000)
    ap.add_argument("--parts", type=int, default=50)
    ap.add_argument("--gate", type=float, default=0.0638)
    ap.add_argument("--sell-buffer", type=float, default=0.001)
    ap.add_argument(
        "--bid-mode",
        default="off",
        choices=["off", "compete", "new_first", "bid_first"],
    )
    ap.add_argument("--bid-threshold", type=float, default=0.04)
    ap.add_argument("--max-bid", type=int, default=3)
    ap.add_argument("--max-etfs", type=int, default=0, help="0 = all universe")
    ap.add_argument("--sweep", action="store_true", help="run parameter sweep")
    ap.add_argument("--refresh-cache", action="store_true")
    args = ap.parse_args()

    codes = load_universe()
    if args.max_etfs > 0:
        codes = codes[: args.max_etfs]

    print(f"Universe: {len(codes)} ETFs")
    panel = load_or_download_panel(
        codes, args.data_start, args.end, refresh=args.refresh_cache
    )
    print(f"Usable series: {len(panel)}")
    if len(panel) < 5:
        print("Not enough data — abort")
        sys.exit(1)

    start = pd.Timestamp(args.start)
    end = (
        pd.Timestamp(args.end)
        if args.end
        else pd.Timestamp(pd.Timestamp.today().date())
    )
    days = trading_days(panel, start, end)
    if not days:
        print("No trading days in window")
        sys.exit(1)
    print(f"Backtest window: {days[0].date()} → {days[-1].date()} ({len(days)} days)")

    OUT_DIR.mkdir(exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")

    if args.sweep:
        df = run_sweep(
            panel, days, capital=args.capital, sell_buffer=args.sell_buffer
        )
        tag = f"{days[0].strftime('%Y%m%d')}_{days[-1].strftime('%Y%m%d')}"
        out_csv = OUT_DIR / f"sweep_{tag}_{stamp}.csv"
        out_json = OUT_DIR / f"sweep_{tag}_{stamp}.json"
        df.to_csv(out_csv, index=False)
        payload = {
            "window": {"start": str(days[0].date()), "end": str(days[-1].date())},
            "capital": args.capital,
            "n_runs": len(df),
            "best": summary_row(
                # rebuild from top csv row fields already in df
                {**df.sort_values("total_return_pct", ascending=False).iloc[0].to_dict(),
                 "mode": df.sort_values("total_return_pct", ascending=False).iloc[0]["rank"]}
            ) if len(df) else {},
            "top15": df.sort_values("total_return_pct", ascending=False)
            .head(15)
            .to_dict(orient="records"),
        }
        # fix best using proper fields
        if len(df):
            best = df.sort_values("total_return_pct", ascending=False).iloc[0]
            payload["best"] = best.to_dict()
        out_json.write_text(json.dumps(payload, indent=2, default=str))
        print_top(df)
        print(f"\nSaved: {out_csv}")
        print(f"Saved: {out_json}")
        return

    results = {}
    for mode in ("rsi", "dma"):
        print(f"\n▶ Running {mode.upper()} bid={args.bid_mode}...")
        results[mode] = run_strategy(
            mode,
            panel,
            days,
            initial_capital=args.capital,
            parts=args.parts,
            gate=args.gate,
            sell_buffer=args.sell_buffer,
            bid_mode=args.bid_mode,
            bid_threshold=args.bid_threshold,
            max_bid=args.max_bid,
        )

    out_json = OUT_DIR / f"rsi_vs_dma_shop_{stamp}.json"
    summary = {}
    for mode, res in results.items():
        pd.DataFrame(res["equity_curve"]).to_csv(
            OUT_DIR / f"equity_{mode}_{stamp}.csv", index=False
        )
        pd.DataFrame(res["buy_log"]).to_csv(
            OUT_DIR / f"buys_{mode}_{stamp}.csv", index=False
        )
        pd.DataFrame(res["sell_log"]).to_csv(
            OUT_DIR / f"sells_{mode}_{stamp}.csv", index=False
        )
        slim = {
            k: v
            for k, v in res.items()
            if k not in {"buy_log", "sell_log", "equity_curve"}
        }
        slim["buys_file"] = f"buys_{mode}_{stamp}.csv"
        slim["sells_file"] = f"sells_{mode}_{stamp}.csv"
        slim["equity_file"] = f"equity_{mode}_{stamp}.csv"
        summary[mode] = slim

    out_json.write_text(json.dumps(summary, indent=2))
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for mode in ("rsi", "dma"):
        r = summary[mode]
        print(
            f"\n{mode.upper()}  bid={r['bid_mode']}\n"
            f"  Period        {r['start']} → {r['end']}\n"
            f"  Final equity  ₹{r['final_equity']:,.2f}\n"
            f"  Total return  {r['total_return_pct']}%\n"
            f"  CAGR          {r['cagr_pct']}%\n"
            f"  Max DD        {r['max_drawdown_pct']}%\n"
            f"  Buys (new/bid){r['new_buys']}/{r['bid_buys']}  Sells {r['sells']}\n"
            f"  Growth booked ₹{r['total_growth']:,.2f}\n"
            f"  Final ticket  ₹{r['final_ticket']:,.2f}\n"
            f"  Open lots     {r['open_positions']}"
        )
    print(f"\nSaved: {out_json}")


if __name__ == "__main__":
    main()
