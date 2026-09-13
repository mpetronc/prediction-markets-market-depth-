"""Offline checks for the ETH hourly fixed-strike collection pipeline."""

from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


DATA_COLLECTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_COLLECTION))

from _pipeline_common import RunLog  # noqa: E402
from fetch_bbo import normalize_snapshot  # noqa: E402
from fetch_trades import normalize_kalshi  # noqa: E402
from find_kalshi_crypto_markets import (  # noqa: E402
    kalshi_event_ticker,
    kalshi_market_ticker,
    parse_kalshi_strike,
)
from find_polymarket_crypto_markets import (  # noqa: E402
    normalize_market as normalize_polymarket_market,
    parse_strike,
)


class PipelineLogicTests(unittest.TestCase):
    def test_run_log_writes_progress_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_log = RunLog("unit_test", Path(directory))
            run_log.progress(2, 4, "rows=20 pages=3")
            log_path = run_log.path
            run_log.close()

            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Progress 2/4 (50.0%)", contents)
            self.assertIn("rows=20 pages=3", contents)
            self.assertNotIn("x-api-key", contents.lower())

    def test_native_kalshi_ticker_uses_eastern_expiration_hour(self) -> None:
        expiration = "2026-08-10T12:00:00Z"

        self.assertEqual(kalshi_event_ticker(expiration), "KXETHD-26AUG1008")
        self.assertEqual(
            kalshi_market_ticker(expiration, "2000"),
            "KXETHD-26AUG1008-T1999.99",
        )

    def test_both_platform_titles_resolve_to_same_strike(self) -> None:
        self.assertEqual(
            parse_strike("Ethereum above $2,000 on August 10?"),
            Decimal("2000"),
        )
        self.assertEqual(parse_kalshi_strike("$2,000 or above"), Decimal("2000"))

    def test_polymarket_resolution_source_is_asset_specific(self) -> None:
        market = {
            "start_time": "2026-08-11T10:00:00Z",
            "end_time": "2026-08-11T12:00:00Z",
            "title": "Ethereum above $2,000 on August 11?",
            "description": "Resolution uses the Binance ETH/USDT 1 hour candle.",
            "tags": ["1H", "Multi Strikes"],
            "condition_id": "condition",
            "outcomes": [
                {"label": "Yes", "token_id": "yes"},
                {"label": "No", "token_id": "no"},
            ],
        }

        row, reason = normalize_polymarket_market(market)
        self.assertIsNotNone(row, reason)

        market["description"] = "Resolution uses the Binance BTC/USDT 1 hour candle."
        row, reason = normalize_polymarket_market(market)
        self.assertIsNone(row)
        self.assertIn("ETH/USDT", reason)

    def test_kalshi_trade_prefers_exact_fixed_point_size(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "ETH",
            "family": "hourly_fixed_strike",
            "kalshi_ticker": "KXETHD-TEST",
        }
        trade = {
            "created_time": 1_786_946_400,
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
            "asset": "ETH",
            "family": "hourly_fixed_strike",
            "kalshi_ticker": "KXETHD-TEST",
        }
        trade = {
            "created_time": 1_786_946_400,
            "yes_price": "0.50",
            "count": 14,
        }

        row = normalize_kalshi(pair, trade)

        self.assertEqual(str(row["size"]), "14")
        self.assertEqual(row["size_source"], "count")
        self.assertFalse(row["size_is_exact"])

    def test_kalshi_subcent_level_preserves_fractional_price_and_size(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "ETH",
            "family": "hourly_fixed_strike",
            "polymarket_condition_id": "condition",
        }
        snapshot = {
            "timestamp": 1_786_946_400_000,
            "yes_bids": [{"price": "36.50", "size": "63.21"}],
            "yes_asks": [{"price": "37.00", "size": "11.75"}],
            "source": "websocket",
        }

        row = normalize_snapshot(pair, "kalshi", "yes", "ticker", snapshot)

        self.assertAlmostEqual(row["best_bid"], 0.365)
        self.assertAlmostEqual(row["best_ask"], 0.37)
        self.assertAlmostEqual(row["bid_depth_contracts"], 63.21)
        self.assertEqual(row["capture_source"], "websocket")


if __name__ == "__main__":
    unittest.main()
