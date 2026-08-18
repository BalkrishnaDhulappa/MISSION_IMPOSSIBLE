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

    def test_uses_holdings_last_price_when_kite_ltp_empty(self):
        from engine import merge_holdings_ltps, pick_sell_candidate

        holdings = {
            "NSE:MODEFENCE": {"qty": 59, "avg": 102.19, "ltp": 109.70},
            "NSE:DEFENCE": {"qty": 77, "avg": 78.44, "ltp": 83.52},
        }
        ltps = merge_holdings_ltps(holdings, {})
        w = pick_sell_candidate(holdings, ltps, set(holdings), 0.0638)
        self.assertIsNotNone(w)
        self.assertEqual(w["code"], "NSE:MODEFENCE")
        self.assertGreater(w["profit_pct"], 0.0638)

    def test_lookup_ltp_keys_without_exchange_prefix(self):
        from engine import _lookup_quote, _parse_ltp_row

        raw = {"MODEFENCE": {"last_price": 109.7}}
        row = _lookup_quote(raw, "NSE:MODEFENCE")
        self.assertAlmostEqual(_parse_ltp_row(row), 109.7)


class TestManualSellReconcile(unittest.TestCase):
    """M2 must not wipe state for same-day buys missing from holdings yet."""

    def test_skips_when_buy_today_no_sell(self):
        from unittest.mock import MagicMock, patch
        from engine import reconcile_manual_sells

        kite = MagicMock()
        state = {
            "NSE:SBINEQWETF": {
                "last_buy": 34.15,
                "broker_avg": 34.15,
                "invested": 6000,
                "bid_count": 0,
            }
        }
        holdings = {}  # lag: not in holdings yet
        ledger = default_ledger(300000, 50)
        config = {"dp_flat_fallback": 15.34}

        with patch("engine.find_today_trades") as mock_trades, patch(
            "engine.send_telegram"
        ), patch("engine.save_state"):
            mock_trades.side_effect = lambda kite, code, side: (
                [{"quantity": 175, "average_price": 34.15}]
                if side.upper() == "BUY"
                else []
            )
            booked, cleaned, skipped = reconcile_manual_sells(
                kite,
                state,
                holdings,
                {"NSE:SBINEQWETF"},
                ledger,
                config,
            )

        self.assertEqual(skipped, ["NSE:SBINEQWETF"])
        self.assertEqual(booked, [])
        self.assertEqual(cleaned, [])
        self.assertIn("NSE:SBINEQWETF", state)  # state kept

    def test_cleans_when_truly_gone_no_trades(self):
        from unittest.mock import MagicMock, patch
        from engine import reconcile_manual_sells

        kite = MagicMock()
        state = {
            "NSE:ALPHA": {
                "last_buy": 50.0,
                "broker_avg": 50.0,
                "invested": 5000,
                "bid_count": 0,
            }
        }
        holdings = {}
        ledger = default_ledger(300000, 50)
        config = {"dp_flat_fallback": 15.34}

        with patch("engine.find_today_trades", return_value=[]), patch(
            "engine.find_today_sell_trades", return_value=[]
        ), patch("engine.send_telegram"), patch("engine.save_state"):
            booked, cleaned, skipped = reconcile_manual_sells(
                kite, state, holdings, {"NSE:ALPHA"}, ledger, config
            )

        self.assertEqual(cleaned, ["NSE:ALPHA"])
        self.assertEqual(skipped, [])
        self.assertNotIn("NSE:ALPHA", state)


if __name__ == "__main__":
    unittest.main()
