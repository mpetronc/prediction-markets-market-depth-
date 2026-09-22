"""Offline tests for political-market analysis functions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


POLITICAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POLITICAL_ROOT))

import analysis  # noqa: E402


class PoliticalAnalysisTests(unittest.TestCase):
    def test_simulated_execution_walks_best_prices_first(self) -> None:
        levels = [
            {"price": 0.55, "size": 40},
            {"price": 0.56, "size": 80},
        ]
        average, fill = analysis.simulate_execution(
            levels, 100, ascending=True
        )
        self.assertAlmostEqual(fill, 1.0)
        self.assertAlmostEqual(average, (0.55 * 40 + 0.56 * 60) / 100)

    def test_incomplete_book_has_partial_fill_ratio(self) -> None:
        average, fill = analysis.simulate_execution(
            [{"price": 0.45, "size": 25}], 100, ascending=False
        )
        self.assertAlmostEqual(average, 0.45)
        self.assertAlmostEqual(fill, 0.25)

    def test_hourly_panel_is_balanced_and_does_not_backfill_quotes(self) -> None:
        market_map = pd.DataFrame(
            [
                {
                    "pair_id": "PAIR",
                    "event_key": "event",
                    "family": "politics",
                    "group_id": "group",
                    "canonical_outcome": "Candidate",
                    "analysis_tier": "primary",
                }
            ]
        )
        start = pd.Timestamp("2026-08-10T11:00:00Z")
        treatment = pd.Timestamp("2026-08-10T12:00:00Z")
        end = pd.Timestamp("2026-08-10T14:00:00Z")
        trades = pd.DataFrame(
            [
                {
                    "pair_id": "PAIR",
                    "platform": "kalshi",
                    "hour_utc": start,
                    "trade_id": "trade",
                    "size": 2.0,
                    "notional_usd": 1.0,
                    "canonical_yes_price": 0.5,
                    "price_x_size": 1.0,
                }
            ]
        )
        bbo = pd.DataFrame(
            [
                {
                    "pair_id": "PAIR",
                    "platform": "kalshi",
                    "hour_utc": treatment,
                    "timestamp_utc": treatment,
                    "spread": 0.02,
                    "mid_price": 0.5,
                    "best_bid_depth_contracts": 10.0,
                    "best_ask_depth_contracts": 10.0,
                    "full_bid_depth_contracts": 20.0,
                    "full_ask_depth_contracts": 20.0,
                    "full_bid_depth_notional": 9.0,
                    "full_ask_depth_notional": 11.0,
                    "full_depth_imbalance": 0.0,
                }
            ]
        )
        panel = analysis.build_hourly_panel(
            market_map, trades, bbo, start, treatment, end, ()
        )
        self.assertEqual(len(panel), 6)
        poly = panel.loc[panel["platform"].eq("polymarket")]
        self.assertTrue(poly["mid_price_ffill"].isna().all())
        kalshi = panel.loc[panel["platform"].eq("kalshi")].reset_index(drop=True)
        self.assertTrue(pd.isna(kalshi.loc[0, "mid_price_ffill"]))
        self.assertAlmostEqual(kalshi.loc[1, "mid_price_ffill"], 0.5)
        self.assertEqual(kalshi.loc[0, "trade_records"], 1)


if __name__ == "__main__":
    unittest.main()
