"""Unit tests for scanner filters (D1=A)."""

from scanner import ScanConfig, ScanRow, dist_200_pct, is_car_rising, passes_scanner_filters, rank_candidates


def _row(**kwargs) -> ScanRow:
    defaults = dict(
        symbol="TEST",
        cmp=110.0,
        dma_30=105.0,
        dma_50=100.0,
        dma_200=100.0,
        car_last_n=tuple(100.0 + i for i in range(10)),
    )
    defaults.update(kwargs)
    return ScanRow(**defaults)


def test_dist_200_pct():
    assert dist_200_pct(110, 100) == 10.0


def test_is_car_rising_strict():
    assert is_car_rising([1, 2, 3, 4])
    assert not is_car_rising([1, 2, 2, 3])
    assert not is_car_rising([1])


def test_passes_d1_a_filters():
    cfg = ScanConfig()
    assert passes_scanner_filters(_row(), cfg)


def test_fails_when_dma30_not_above_dma50():
    cfg = ScanConfig(require_dma30_gt_dma50=True)
    assert not passes_scanner_filters(_row(dma_30=99, dma_50=100), cfg)


def test_fails_when_dist_200_above_cap():
    cfg = ScanConfig(max_dist_200_pct=10.0)
    assert not passes_scanner_filters(_row(cmp=112, dma_200=100), cfg)


def test_fails_when_car_not_rising():
    flat = tuple(100.0 for _ in range(10))
    assert not passes_scanner_filters(_row(car_last_n=flat), ScanConfig())


def test_rank_candidates_ascending_dist():
    cfg = ScanConfig()
    rows = [
        _row(symbol="FAR", cmp=109, dma_30=108, dma_200=100),
        _row(symbol="NEAR", cmp=106, dma_30=105, dma_200=100),
    ]
    ranked = rank_candidates(rows, cfg)
    assert [r.symbol for r in ranked] == ["NEAR", "FAR"]
