"""Scanner filter logic (D1=A) — pure functions, no data fetch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


def dist_200_pct(cmp: float, dma_200: float) -> float:
    """Percent distance of CMP above 200 DMA."""
    if dma_200 <= 0:
        raise ValueError("dma_200 must be positive")
    return round(((cmp - dma_200) / dma_200) * 100, 4)


def is_car_rising(car_values: Sequence[float]) -> bool:
    """True iff CAR values are strictly monotonic increasing."""
    if len(car_values) < 2:
        return False
    return all(car_values[i] < car_values[i + 1] for i in range(len(car_values) - 1))


@dataclass(frozen=True)
class ScanRow:
    symbol: str
    cmp: float
    dma_30: float
    dma_50: float
    dma_200: float
    car_last_n: tuple[float, ...]


@dataclass(frozen=True)
class ScanConfig:
    require_dma30_gt_dma50: bool = True
    max_dist_200_pct: float = 10.0
    car_rising_days: int = 10


def passes_scanner_filters(row: ScanRow, cfg: ScanConfig) -> bool:
    """D1=A: rising CAR, CMP > 30/200 DMA, 30>50 DMA, dist_200 <= cap."""
    if len(row.car_last_n) < cfg.car_rising_days:
        return False
    tail = row.car_last_n[-cfg.car_rising_days :]
    if not is_car_rising(tail):
        return False
    if row.cmp <= row.dma_30 or row.cmp <= row.dma_200:
        return False
    if cfg.require_dma30_gt_dma50 and row.dma_30 <= row.dma_50:
        return False
    d200 = dist_200_pct(row.cmp, row.dma_200)
    if d200 > cfg.max_dist_200_pct:
        return False
    return True


def rank_candidates(rows: Iterable[ScanRow], cfg: ScanConfig) -> list[ScanRow]:
    """Filter and sort by ascending distance from 200 DMA."""
    passed = [r for r in rows if passes_scanner_filters(r, cfg)]
    return sorted(passed, key=lambda r: dist_200_pct(r.cmp, r.dma_200))
