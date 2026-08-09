#!/usr/bin/env python3
"""Unit tests for FIRE ETF profit target (6.28% → 3.14% when capital doubles)."""

import unittest

from engine import (
    capital_is_doubled,
    ensure_original_invested,
    pick_sell_candidate,
    select_profit_target_pct,
)


class TestCapitalDoubleProfitTarget(unittest.TestCase):
    def test_select_keeps_628_until_double(self):
        self.assertEqual(select_profit_target_pct(6000, 6000), 0.0628)
        self.assertEqual(select_profit_target_pct(6000, 11999), 0.0628)

    def test_select_halves_to_314_when_doubled(self):
        self.assertEqual(select_profit_target_pct(6000, 12000), 0.0314)
        self.assertEqual(select_profit_target_pct(6000, 18000), 0.0314)

    def test_select_defaults_when_original_missing(self):
        self.assertEqual(select_profit_target_pct(None, 12000), 0.0628)
        self.assertEqual(select_profit_target_pct(0, 12000), 0.0628)

    def test_capital_is_doubled_boundary(self):
        self.assertFalse(capital_is_doubled(6000, 11999.99))
        self.assertTrue(capital_is_doubled(6000, 12000))
        self.assertFalse(capital_is_doubled(0, 1000))

    def test_ensure_original_freezes_once(self):
        s = {}
        self.assertTrue(ensure_original_invested(s, 6000))
        self.assertEqual(s["original_invested"], 6000)
        self.assertFalse(ensure_original_invested(s, 9000))
        self.assertEqual(s["original_invested"], 6000)


class TestPickSellCandidate(unittest.TestCase):
    def setUp(self):
        self.config = {
            "profit_target_pct": 0.0628,
            "profit_target_pct_when_capital_doubled": 0.0314,
        }

    def test_no_sell_below_628(self):
        holdings = {"NSE:A": {"qty": 10, "avg": 100.0}}
        ranked = [{"code": "NSE:A", "cmp": 106.0}]  # 6.0%
        state = {"NSE:A": {"invested": 1000, "original_invested": 1000}}
        self.assertIsNone(pick_sell_candidate(holdings, ranked, state, self.config))

    def test_sell_at_exactly_628(self):
        holdings = {"NSE:A": {"qty": 10, "avg": 100.0}}
        ranked = [{"code": "NSE:A", "cmp": 106.28}]
        state = {"NSE:A": {"invested": 1000, "original_invested": 1000}}
        winner = pick_sell_candidate(holdings, ranked, state, self.config)
        self.assertIsNotNone(winner)
        self.assertEqual(winner["code"], "NSE:A")
        self.assertEqual(winner["target"], 0.0628)
        self.assertFalse(winner["capital_doubled"])

    def test_halved_target_when_capital_doubled(self):
        # 4% profit: not enough for 6.28%, enough for 3.14% after double
        holdings = {"NSE:A": {"qty": 20, "avg": 100.0}}
        ranked = [{"code": "NSE:A", "cmp": 104.0}]
        state = {
            "NSE:A": {
                "invested": 2000,
                "original_invested": 1000,  # capital doubled via BIDs
            }
        }
        winner = pick_sell_candidate(holdings, ranked, state, self.config)
        self.assertIsNotNone(winner)
        self.assertEqual(winner["target"], 0.0314)
        self.assertTrue(winner["capital_doubled"])

    def test_still_needs_628_when_not_doubled(self):
        holdings = {"NSE:A": {"qty": 10, "avg": 100.0}}
        ranked = [{"code": "NSE:A", "cmp": 104.0}]  # 4%
        state = {"NSE:A": {"invested": 1000, "original_invested": 1000}}
        self.assertIsNone(pick_sell_candidate(holdings, ranked, state, self.config))

    def test_picks_most_profitable_among_eligible(self):
        holdings = {
            "NSE:LOW": {"qty": 10, "avg": 100.0},
            "NSE:HIGH": {"qty": 5, "avg": 100.0},
        }
        ranked = [
            {"code": "NSE:LOW", "cmp": 107.0},   # 7%
            {"code": "NSE:HIGH", "cmp": 110.0},  # 10%
        ]
        state = {
            "NSE:LOW": {"invested": 1000, "original_invested": 1000},
            "NSE:HIGH": {"invested": 500, "original_invested": 500},
        }
        winner = pick_sell_candidate(holdings, ranked, state, self.config)
        self.assertEqual(winner["code"], "NSE:HIGH")
        self.assertAlmostEqual(winner["profit_pct"], 0.10, places=4)


if __name__ == "__main__":
    unittest.main()
