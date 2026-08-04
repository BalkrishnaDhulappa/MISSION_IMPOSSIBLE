"""Ledger tests for live sell compounding."""

from datetime import date, timedelta

from config import load_config
from ledger import Ledger


def test_record_win_and_close_position(tmp_path):
    db = tmp_path / "t.sqlite"
    ledger = Ledger(db)
    cfg = load_config()
    ledger.ensure_step(1, 15000)
    buy = date(2026, 1, 6)
    ledger.add_position("RELIANCE", buy, 10, 1500.0, 4500.0)

    sell = buy + timedelta(days=14)
    summary = ledger.record_win_and_close_position(
        "RELIANCE", exit_value=16500.0, sell_date=sell, cfg=cfg
    )
    assert summary is not None
    assert summary["force_tag"] == "F"
    assert summary["next_ticket"] > 15000
    assert ledger.list_positions(status="open_mtf") == []
    closed = ledger.list_positions(status="closed")
    assert len(closed) == 1
    assert closed[0].force_tag == "F"
