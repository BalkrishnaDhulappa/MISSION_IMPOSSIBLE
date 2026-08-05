"""Fetch OHLC and compute CAR/DMA indicators (port of colab_code)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from scanner import ScanConfig, ScanRow, dist_200_pct, passes_scanner_filters, rank_candidates

DEFAULT_UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "resources" / "fo_universe.json"


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    cmp: float
    dma_30: float
    dma_50: float
    dma_200: float
    dist_200_pct: float
    car_status: str
    scan_date: str


def load_universe(path: str | Path | None = None) -> list[str]:
    p = Path(path) if path else DEFAULT_UNIVERSE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data["universe"])


def yfinance_ticker(nse_symbol: str) -> str:
    return f"{nse_symbol}.NS" if not nse_symbol.endswith(".NS") else nse_symbol


def _close_series(df: pd.DataFrame) -> pd.Series:
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.squeeze(axis=1)
    return close.dropna()


def _high_series(df: pd.DataFrame) -> pd.Series:
    high = df["High"]
    if isinstance(high, pd.DataFrame):
        high = high.squeeze(axis=1)
    return high.dropna()


def analyze_ohlcv(df: pd.DataFrame, symbol: str, *, car_rising_days: int = 10) -> ScanRow | None:
    """Compute indicators from daily OHLCV; return ScanRow or None if insufficient data."""
    if df.empty or len(df) < 200:
        return None

    close = _close_series(df)
    if len(close) < 200:
        return None

    cmp = float(close.iloc[-1])
    dma_30 = float(close.rolling(window=30).mean().iloc[-1])
    dma_50 = float(close.rolling(window=50).mean().iloc[-1])
    dma_200 = float(close.rolling(window=200).mean().iloc[-1])

    last_1y = df.tail(252)
    highs = _high_series(last_1y)
    if highs.empty:
        return None
    high_date = highs.idxmax()
    car_data = close.loc[high_date:]
    if len(car_data) < car_rising_days:
        return None

    car_values = car_data.expanding().mean()
    last_n = tuple(float(x) for x in car_values.tail(car_rising_days).tolist())

    return ScanRow(
        symbol=symbol,
        cmp=round(cmp, 2),
        dma_30=round(dma_30, 2),
        dma_50=round(dma_50, 2),
        dma_200=round(dma_200, 2),
        car_last_n=last_n,
    )


def fetch_ohlcv_yfinance(ticker: str, *, period: str = "2y") -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError("yfinance required for live scan: pip install yfinance") from exc

    try:
        df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def scan_symbol(
    symbol: str,
    *,
    fetcher: Any = None,
    car_rising_days: int = 10,
) -> ScanRow | None:
    ticker = yfinance_ticker(symbol)
    if fetcher is not None:
        df = fetcher(ticker)
    else:
        df = fetch_ohlcv_yfinance(ticker)
    if df is None:
        return None
    return analyze_ohlcv(df, symbol, car_rising_days=car_rising_days)


def scan_universe(
    symbols: Sequence[str],
    cfg: ScanConfig,
    *,
    fetcher: Any = None,
    limit: int | None = None,
) -> list[ScanCandidate]:
    """Scan symbols; return D1=A candidates ranked by dist_200 ascending."""
    rows: list[ScanRow] = []
    subset = list(symbols[:limit] if limit else symbols)
    for sym in subset:
        row = scan_symbol(sym, fetcher=fetcher, car_rising_days=cfg.car_rising_days)
        if row is not None:
            rows.append(row)

    ranked = rank_candidates(rows, cfg)
    today = datetime.now().date().isoformat()
    out: list[ScanCandidate] = []
    for r in ranked:
        rising = passes_scanner_filters(r, cfg)
        out.append(
            ScanCandidate(
                symbol=r.symbol,
                cmp=r.cmp,
                dma_30=r.dma_30,
                dma_50=r.dma_50,
                dma_200=r.dma_200,
                dist_200_pct=dist_200_pct(r.cmp, r.dma_200),
                car_status="Positive" if rising else "Negative",
                scan_date=today,
            )
        )
    return out


def candidates_to_dict(candidates: list[ScanCandidate], *, as_of: date | None = None) -> dict[str, Any]:
    as_of_s = (as_of or date.today()).isoformat()
    return {
        "scan_date": as_of_s,
        "count": len(candidates),
        "candidates": [
            {
                "symbol": c.symbol,
                "cmp": c.cmp,
                "dma_30": c.dma_30,
                "dma_50": c.dma_50,
                "dma_200": c.dma_200,
                "dist_200_pct": c.dist_200_pct,
                "car_status": c.car_status,
            }
            for c in candidates
        ],
    }


def save_scan_result(candidates: list[ScanCandidate], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(candidates_to_dict(candidates), indent=2), encoding="utf-8")


def load_scan_result(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
