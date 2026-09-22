"""Offline checks for the SOL 15-minute collection pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


DATA_COLLECTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_COLLECTION))

from _pipeline_common import RunLog, TREATMENT_TIME, treatment_period  # noqa: E402
from fetch_bbo import normalize_snapshot  # noqa: E402
from fetch_trades import normalize_kalshi  # noqa: E402
from find_kalshi_crypto_markets import expected_market_tickers  # noqa: E402


class PipelineLogicTests(unittest.TestCase):
    def test_run_log_writes_progress_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_log = RunLog("unit_test", Path(directory))
            run_log.progress(1, 4, "rows=12 pages=2")
            log_path = run_log.path
            run_log.close()

            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Progress 1/4 (25.0%)", contents)
            self.assertIn("rows=12 pages=2", contents)
            self.assertNotIn("x-api-key", contents.lower())

    def test_expected_ticker_grid_covers_exact_two_week_window(self) -> None:
        tickers = expected_market_tickers()

        self.assertEqual(len(tickers), 14 * 24 * 4)
        self.assertEqual(tickers[0], "KXSOL15M-26AUG100715-15")
        self.assertEqual(tickers[-1], "KXSOL15M-26AUG240700-00")
        self.assertEqual(len(tickers), len(set(tickers)))

    def test_treatment_cutoff_is_post_at_exact_change_time(self) -> None:
        self.assertEqual(treatment_period(TREATMENT_TIME.timestamp() - 1), "pre")
        self.assertEqual(treatment_period(TREATMENT_TIME.timestamp()), "post")

    def test_kalshi_boundary_placeholders_do_not_define_bbo(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "SOL",
            "family": "crypto_up_down_15m",
            "polymarket_condition_id": "condition",
        }
        snapshot = {
            "timestamp": int(TREATMENT_TIME.timestamp() * 1_000),
            "yes_bids": [
                {"price": 0, "size": 10},
                {"price": 99, "size": 2},
            ],
            "yes_asks": [{"price": 100, "size": 10}],
        }

        row = normalize_snapshot(pair, "kalshi", "yes", "ticker", snapshot)

        self.assertEqual(row["best_bid"], 0.99)
        self.assertIsNone(row["best_ask"])
        self.assertIsNone(row["spread"])
        self.assertEqual(json.loads(row["bids_json"]), [{"price": 0.99, "size": 2.0}])
        self.assertEqual(json.loads(row["asks_json"]), [])

    def test_kalshi_subcent_level_preserves_fractional_price_and_size(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "SOL",
            "family": "crypto_up_down_15m",
            "polymarket_condition_id": "condition",
        }
        snapshot = {
            "timestamp": int(TREATMENT_TIME.timestamp() * 1_000),
            "yes_bids": [{"price": "36.50", "size": "63.21"}],
            "yes_asks": [{"price": "37.00", "size": "11.75"}],
            "source": "websocket",
        }

        row = normalize_snapshot(pair, "kalshi", "yes", "ticker", snapshot)

        self.assertAlmostEqual(row["best_bid"], 0.365)
        self.assertAlmostEqual(row["best_ask"], 0.37)
        self.assertAlmostEqual(row["bid_depth_contracts"], 63.21)
        self.assertEqual(row["capture_source"], "websocket")

    def test_kalshi_trade_prefers_exact_fixed_point_size(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "SOL",
            "family": "crypto_up_down_15m",
            "kalshi_ticker": "KXSOL15M-TEST",
        }
        trade = {
            "created_time": int(TREATMENT_TIME.timestamp()),
            "yes_price": "0.50",
            "count": 14,
            "count_fp": "13.79",
        }

        row = normalize_kalshi(pair, trade)

        self.assertEqual(str(row["size"]), "13.79")
        self.assertEqual(row["size_source"], "count_fp")
        self.assertTrue(row["size_is_exact"])
        self.assertEqual(str(row["notional_usd"]), "6.8950")

    def test_kalshi_trade_marks_integer_fallback_as_inexact(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "SOL",
            "family": "crypto_up_down_15m",
            "kalshi_ticker": "KXSOL15M-TEST",
        }
        trade = {
            "created_time": int(TREATMENT_TIME.timestamp()),
            "yes_price": "0.50",
            "count": 14,
        }

        row = normalize_kalshi(pair, trade)

        self.assertEqual(str(row["size"]), "14")
        self.assertEqual(row["size_source"], "count")
        self.assertFalse(row["size_is_exact"])


if __name__ == "__main__":
    unittest.main()
