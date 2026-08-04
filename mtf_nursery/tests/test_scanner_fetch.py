"""Tests for scanner_fetch with fixture OHLCV (no network)."""

import numpy as np
import pandas as pd

from scanner import ScanConfig
from scanner_fetch import analyze_ohlcv, candidates_to_dict, load_universe, scan_universe


def _rising_df(rows: int = 260) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=rows, freq="B")
    # Gentle uptrend keeps dist_200 under 10% while CAR rises after prior high
    close = np.linspace(95.0, 108.0, rows)
    high = close.copy()
    peak_idx = rows - 40
    high[peak_idx] = close.max() + 15
    high[peak_idx + 1 :] = close[peak_idx + 1 :] + 0.5
    return pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=dates,
    )


def test_analyze_ohlcv_returns_scan_row():
    df = _rising_df()
    row = analyze_ohlcv(df, "TEST")
    assert row is not None
    assert row.cmp > row.dma_30 > row.dma_50
    assert row.cmp > row.dma_200
    assert len(row.car_last_n) == 10


def test_scan_universe_with_injected_fetcher():
    def fetcher(_ticker: str):
        return _rising_df()

    cfg = ScanConfig()
    cands = scan_universe(["AAA", "BBB"], cfg, fetcher=fetcher)
    assert len(cands) >= 1
    assert cands[0].symbol in ("AAA", "BBB")
    assert cands[0].car_status == "Positive"


def test_load_universe_count():
    syms = load_universe()
    assert len(syms) == 210


def test_candidates_to_dict():
    from scanner_fetch import ScanCandidate

    c = ScanCandidate("X", 100, 95, 90, 85, 5.0, "Positive", "2026-08-04")
    d = candidates_to_dict([c])
    assert d["count"] == 1
    assert d["candidates"][0]["symbol"] == "X"
