"""Offline checks for the ETH 15-minute collection pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


DATA_COLLECTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_COLLECTION))

from _pipeline_common import RunLog, TREATMENT_TIME, treatment_period  # noqa: E402
from buildmarkets.build_market_map import OUT_PATH as MARKET_MAP_OUTPUT  # noqa: E402
from fetch_bbo import MAP_PATH as BBO_MAP_PATH, normalize_snapshot  # noqa: E402
from fetch_kalshi_native_trades import MAP_PATH as NATIVE_TRADE_MAP_PATH  # noqa: E402
from fetch_trades import MAP_PATH as TRADE_MAP_PATH, normalize_kalshi  # noqa: E402
from find_kalshi_crypto_markets import (  # noqa: E402
    expected_market_tickers,
    normalize_market as normalize_kalshi_market,
)
from find_polymarket_crypto_markets import (  # noqa: E402
    normalize_market as normalize_polymarket_market,
)


class PipelineLogicTests(unittest.TestCase):
    def test_every_collector_uses_the_eth_market_map(self) -> None:
        expected_name = "ETH_market_map.csv"
        self.assertEqual(MARKET_MAP_OUTPUT.name, expected_name)
        self.assertEqual(TRADE_MAP_PATH, MARKET_MAP_OUTPUT)
        self.assertEqual(BBO_MAP_PATH, MARKET_MAP_OUTPUT)
        self.assertEqual(NATIVE_TRADE_MAP_PATH, MARKET_MAP_OUTPUT)

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
        self.assertEqual(tickers[0], "KXETH15M-26AUG100715-15")
        self.assertEqual(tickers[-1], "KXETH15M-26AUG240700-00")
        self.assertEqual(len(tickers), len(set(tickers)))

    def test_treatment_cutoff_is_post_at_exact_change_time(self) -> None:
        self.assertEqual(treatment_period(TREATMENT_TIME.timestamp() - 1), "pre")
        self.assertEqual(treatment_period(TREATMENT_TIME.timestamp()), "post")

    def test_polymarket_discovery_accepts_eth_and_rejects_btc(self) -> None:
        market = {
            "asset": "eth",
            "timeframe": "15m",
            "condition_id": "condition",
            "market_slug": "eth-updown-15m-test",
            "start_time": "2026-08-10T10:00:00Z",
            "end_time": "2026-08-10T11:15:00Z",
            "up_token_id": "up-token",
            "down_token_id": "down-token",
        }

        row, reason = normalize_polymarket_market(market)

        self.assertEqual(reason, "")
        self.assertEqual(row["asset"], "ETH")
        self.assertEqual(row["interval_start_utc"], "2026-08-10T11:00:00Z")

        row, reason = normalize_polymarket_market({**market, "asset": "btc"})
        self.assertIsNone(row)
        self.assertIn("asset is not eth", reason)

    def test_kalshi_discovery_requires_eth_15m_series(self) -> None:
        market = {
            "ticker": "KXETH15M-TEST",
            "event_ticker": "KXETH15M-EVENT",
            "market_id": "market",
            "open_time": "2026-08-10T11:00:00Z",
            "close_time": "2026-08-10T11:15:00Z",
            "event": {"series_ticker": "KXETH15M"},
        }

        row, reason = normalize_kalshi_market(market)

        self.assertEqual(reason, "")
        self.assertEqual(row["asset"], "ETH")
        self.assertEqual(row["series_ticker"], "KXETH15M")

        row, reason = normalize_kalshi_market(
            {**market, "event": {"series_ticker": "KXBTC15M"}}
        )
        self.assertIsNone(row)
        self.assertIn("wrong series ticker", reason)

    def test_kalshi_boundary_placeholders_do_not_define_bbo(self) -> None:
        pair = {
            "pair_id": "test-pair",
            "asset": "ETH",
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
            "asset": "ETH",
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
            "asset": "ETH",
            "family": "crypto_up_down_15m",
            "kalshi_ticker": "KXETH15M-TEST",
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
            "asset": "ETH",
            "family": "crypto_up_down_15m",
            "kalshi_ticker": "KXETH15M-TEST",
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
