#!/usr/bin/env python3
"""
Backtest: ETF shop — RSI(14) lowest vs DMA-dip rank
====================================================
Proposed params (agreed):
  - Universe: etf_universe.json
  - Entry: 1 new buy/day, not already held
  - Rank A: lowest RSI(14)  |  Rank B: deepest dip vs 20 DMA
  - Size: WC ₹3L / 50 parts → ticket starts ₹6,000; compounds after sells
  - Exit: LTP >= avg × 1.0638, 1 sell/day, full position, best unrealized %
  - BID: off
  - Charges: CNC sell-side formula (Zerodha-style); no tax set-aside
  - Day order (match live T1): BUY first with today's ticket, then SELL
    (sell growth raises ticket for the *next* day)

Usage:
  python3 backtest_rsi_etf_shop.py
  python3 backtest_rsi_etf_shop.py --start 2022-01-01 --end 2026-08-01
"""

from __future__ import annotations

import argparse
import json
import math
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


@dataclass
class Position:
    code: str
    qty: int
    avg: float
    cost: float


@dataclass
class Ledger:
    working_capital: float
    parts: int = 50
    total_growth: float = 0.0
    sells: list = field(default_factory=list)

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
        if len(out) < 60:
            print(f"    skip short history ({len(out)})")
            continue
        panel[code] = out
        time.sleep(0.15)
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
    """Per-code indicators available on `day`."""
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


def pick_buy(mode: str, snap: dict, held: set[str]) -> str | None:
    cands = [(c, v) for c, v in snap.items() if c not in held]
    if not cands:
        return None
    if mode == "rsi":
        cands.sort(key=lambda x: x[1]["rsi"])  # lowest RSI
    else:
        cands.sort(key=lambda x: x[1]["dip"])  # most negative dip
    return cands[0][0]


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


def run_strategy(
    mode: str,
    panel: dict[str, pd.DataFrame],
    days: list[pd.Timestamp],
    *,
    initial_capital: float,
    parts: int,
    gate: float,
    sell_buffer: float,
) -> dict:
    cash = float(initial_capital)
    ledger = Ledger(working_capital=float(initial_capital), parts=parts)
    positions: dict[str, Position] = {}
    buys = []
    sells = []
    equity_curve = []

    for day in days:
        snap = snapshot(panel, day)
        ticket = ledger.ticket  # freeze for today's buy (T1)

        # 1) BUY first
        code = pick_buy(mode, snap, set(positions.keys()))
        if code is not None and cash >= ticket:
            px = snap[code]["close"]
            qty = int(math.floor(ticket / px))
            if qty >= 1:
                cost = qty * px
                if cost <= cash:
                    cash -= cost
                    positions[code] = Position(code=code, qty=qty, avg=px, cost=cost)
                    buys.append(
                        {
                            "date": day.date().isoformat(),
                            "code": code,
                            "qty": qty,
                            "price": round(px, 4),
                            "cost": round(cost, 2),
                            "ticket": ticket,
                            "rsi": round(snap[code]["rsi"], 2),
                            "dip": round(snap[code]["dip"], 4),
                        }
                    )

        # 2) SELL (growth → next day ticket)
        sell_code = pick_sell(positions, snap, gate)
        if sell_code is not None:
            pos = positions[sell_code]
            ltp = snap[sell_code]["close"]
            # approximate fill at LTP - buffer
            fill = round(ltp * (1.0 - sell_buffer), 4)
            sell_value = pos.qty * fill
            charges = formula_sell_charges(sell_value)["total"]
            growth = round(max(0.0, sell_value - pos.cost - charges), 2)
            cash += sell_value  # charges leave cash; approximate by not adding them back
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

        # mark-to-market equity
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
    cagr = (final_equity / initial_capital) ** (1 / years) - 1.0 if final_equity > 0 else -1.0

    # max drawdown
    eq = pd.Series([e["equity"] for e in equity_curve])
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    return {
        "mode": mode,
        "start": days[0].date().isoformat() if days else None,
        "end": days[-1].date().isoformat() if days else None,
        "trading_days": len(days),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "cash": round(cash, 2),
        "holdings_value": round(holdings_val, 2),
        "open_positions": len(positions),
        "buys": len(buys),
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
            c: {"qty": p.qty, "avg": p.avg, "cost": p.cost} for c, p in positions.items()
        },
    }


def main():
    ap = argparse.ArgumentParser(description="RSI vs DMA ETF shop backtest")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--data-start", default="2019-01-01", help="download warmup start")
    ap.add_argument("--capital", type=float, default=300000)
    ap.add_argument("--parts", type=int, default=50)
    ap.add_argument("--gate", type=float, default=0.0638)
    ap.add_argument("--sell-buffer", type=float, default=0.001)
    ap.add_argument("--max-etfs", type=int, default=0, help="0 = all universe")
    args = ap.parse_args()

    codes = load_universe()
    if args.max_etfs > 0:
        codes = codes[: args.max_etfs]

    print(f"Universe: {len(codes)} ETFs")
    print(f"Downloading from {args.data_start} ...")
    panel = download_panel(codes, args.data_start, args.end)
    print(f"Usable series: {len(panel)}")
    if len(panel) < 5:
        print("Not enough data — abort")
        sys.exit(1)

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp(pd.Timestamp.today().date())
    days = trading_days(panel, start, end)
    print(f"Backtest window: {days[0].date()} → {days[-1].date()} ({len(days)} days)")

    results = {}
    for mode in ("rsi", "dma"):
        print(f"\n▶ Running {mode.upper()} strategy...")
        results[mode] = run_strategy(
            mode,
            panel,
            days,
            initial_capital=args.capital,
            parts=args.parts,
            gate=args.gate,
            sell_buffer=args.sell_buffer,
        )

    OUT_DIR.mkdir(exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out_json = OUT_DIR / f"rsi_vs_dma_shop_{stamp}.json"
    # trim equity curves in summary file size — keep full in separate csv
    summary = {}
    for mode, res in results.items():
        curve = pd.DataFrame(res["equity_curve"])
        curve.to_csv(OUT_DIR / f"equity_{mode}_{stamp}.csv", index=False)
        pd.DataFrame(res["buy_log"]).to_csv(OUT_DIR / f"buys_{mode}_{stamp}.csv", index=False)
        pd.DataFrame(res["sell_log"]).to_csv(OUT_DIR / f"sells_{mode}_{stamp}.csv", index=False)
        slim = {k: v for k, v in res.items() if k not in {"buy_log", "sell_log", "equity_curve"}}
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
            f"\n{mode.upper()}\n"
            f"  Period        {r['start']} → {r['end']}\n"
            f"  Final equity  ₹{r['final_equity']:,.2f}\n"
            f"  Total return  {r['total_return_pct']}%\n"
            f"  CAGR          {r['cagr_pct']}%\n"
            f"  Max DD        {r['max_drawdown_pct']}%\n"
            f"  Buys / Sells  {r['buys']} / {r['sells']}\n"
            f"  Growth booked ₹{r['total_growth']:,.2f}\n"
            f"  Final ticket  ₹{r['final_ticket']:,.2f}\n"
            f"  Open lots     {r['open_positions']}"
        )
    print(f"\nSaved: {out_json}")


if __name__ == "__main__":
    main()
