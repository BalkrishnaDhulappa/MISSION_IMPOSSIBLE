#!/usr/bin/env python3
"""Unit tests for FIRE ETF compounding design (no live broker)."""

import unittest
from pathlib import Path
import tempfile

from charges import charges_from_kite_payload, formula_sell_charges
from compound_ledger import (
    apply_growth,
    compute_growth,
    current_ticket,
    default_ledger,
    load_ledger,
)
from engine import pick_sell_candidate


class TestCharges(unittest.TestCase):
    def test_formula_sell_has_dp_and_stt(self):
        c = formula_sell_charges(100_000)
        self.assertEqual(c["brokerage"], 0.0)
        self.assertAlmostEqual(c["stt"], 100.0, places=2)
        self.assertEqual(c["dp"], 15.34)
        self.assertGreater(c["total"], 100.0)

    def test_kite_payload_adds_dp_when_missing(self):
        payload = [
            {
                "charges": {
                    "brokerage": 0,
                    "transaction_tax": 10,
                    "exchange_turnover_charge": 0.3,
                    "sebi_turnover_charge": 0.01,
                    "stamp_duty": 0,
                    "gst": {"total": 0.05},
                    "total": 10.36,
                }
            }
        ]
        c = charges_from_kite_payload(payload, sell_value=10000, dp_flat=15.34)
        self.assertAlmostEqual(c["total"], 10.36 + 15.34, places=2)
        self.assertEqual(c["dp"], 15.34)


class TestCompoundLedger(unittest.TestCase):
    def test_default_ticket_6000(self):
        led = default_ledger(300000, 50)
        self.assertEqual(led["ticket"], 6000)
        self.assertEqual(current_ticket(led), 6000)

    def test_growth_raises_ticket(self):
        led = default_ledger(300000, 50)
        apply_growth(led, growth=300, sell_record={"code": "NSE:X", "source": "bot"})
        self.assertEqual(led["working_capital"], 300300)
        self.assertEqual(led["ticket"], 6006.0)
        self.assertEqual(led["total_growth"], 300)
        self.assertEqual(len(led["sells"]), 1)

    def test_compute_growth_floors_at_zero(self):
        self.assertEqual(compute_growth(1000, 1000, 20), 0)
        self.assertEqual(compute_growth(1100, 1000, 20), 80)

    def test_load_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "compound_ledger.json"
            led = load_ledger(path, initial_capital=300000, parts=50)
            self.assertTrue(path.exists())
            self.assertEqual(led["ticket"], 6000)


class TestSellSelect(unittest.TestCase):
    def test_requires_638_gate(self):
        holdings = {"NSE:A": {"qty": 10, "avg": 100.0}}
        # 6.37% — not enough
        ltps = {"NSE:A": 106.37}
        self.assertIsNone(
            pick_sell_candidate(holdings, ltps, {"NSE:A"}, 0.0638)
        )
        # 6.38% exact
        ltps = {"NSE:A": 106.38}
        w = pick_sell_candidate(holdings, ltps, {"NSE:A"}, 0.0638)
        self.assertIsNotNone(w)
        self.assertEqual(w["code"], "NSE:A")

    def test_picks_highest_unrealized_pct(self):
        holdings = {
            "NSE:LOW": {"qty": 10, "avg": 100.0},
            "NSE:HIGH": {"qty": 5, "avg": 100.0},
            "NSE:STOCK": {"qty": 1, "avg": 100.0},
        }
        ltps = {
            "NSE:LOW": 107.0,
            "NSE:HIGH": 110.0,
            "NSE:STOCK": 120.0,
        }
        universe = {"NSE:LOW", "NSE:HIGH"}  # STOCK excluded
        w = pick_sell_candidate(holdings, ltps, universe, 0.0638)
        self.assertEqual(w["code"], "NSE:HIGH")

    def test_limit_price_math(self):
        ltp = 100.0
        price = round(ltp * (1 - 0.001), 1)
        self.assertEqual(price, 99.9)


if __name__ == "__main__":
    unittest.main()
