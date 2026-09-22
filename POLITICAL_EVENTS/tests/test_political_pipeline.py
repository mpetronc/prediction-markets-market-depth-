"""Offline tests for the shared political-market pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


POLITICAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POLITICAL_ROOT))

import pipeline  # noqa: E402


class PoliticalPipelineTests(unittest.TestCase):
    def test_configs_have_expected_pair_counts_and_exact_window(self) -> None:
        expected = {
            "BRAZIL_PRESIDENTIAL_ELECTION": (12, 3),
            "ICELAND_EU_REFERENDUM": (1, 1),
            "MASSACHUSETTS_DEMOCRATIC_PRIMARIES": (12, 7),
        }
        for event_name, (pair_count, primary_count) in expected.items():
            config = pipeline.load_config(POLITICAL_ROOT / event_name)
            markets = [
                market
                for group in config["market_groups"]
                for market in group["markets"]
            ]
            self.assertEqual(len(markets), pair_count)
            self.assertEqual(
                sum(market["analysis_tier"] == "primary" for market in markets),
                primary_count,
            )

    def test_cutoff_is_post_at_exact_intervention_time(self) -> None:
        cutoff = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
        self.assertEqual(
            pipeline.classify_period(cutoff.timestamp() - 1, cutoff), "pre"
        )
        self.assertEqual(
            pipeline.classify_period(cutoff.timestamp(), cutoff), "post"
        )

    def test_binary_token_mapping_accepts_json_encoded_arrays(self) -> None:
        yes, no = pipeline.binary_tokens(
            {
                "outcomes": json.dumps(["Yes", "No"]),
                "clobTokenIds": json.dumps(["yes-token", "no-token"]),
            }
        )
        self.assertEqual((yes, no), ("yes-token", "no-token"))

    def test_kalshi_trade_uses_exact_fixed_point_fields(self) -> None:
        pair = {
            "pair_id": "TEST",
            "event_key": "test",
            "family": "politics",
            "group_id": "group",
            "canonical_outcome": "Candidate",
            "analysis_tier": "primary",
            "kalshi_ticker": "KXTEST-CANDIDATE",
        }
        cutoff = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
        row = pipeline.normalize_kalshi_trade(
            pair,
            {
                "trade_id": "trade-1",
                "ticker": "KXTEST-CANDIDATE",
                "created_time": "2026-08-17T11:00:00Z",
                "count_fp": "13.79",
                "yes_price_dollars": "0.5000",
                "taker_side": "yes",
            },
            cutoff,
        )
        self.assertEqual(Decimal(str(row["size"])), Decimal("13.79"))
        self.assertEqual(Decimal(str(row["notional_usd"])), Decimal("6.895000"))
        self.assertEqual(row["observation_period"], "post")
        self.assertFalse(row["treatment_received"])

    def test_polymarket_fills_collapse_to_one_taker_transaction(self) -> None:
        pair = {
            "pair_id": "TEST",
            "event_key": "test",
            "family": "politics",
            "group_id": "group",
            "canonical_outcome": "Candidate",
            "analysis_tier": "primary",
            "polymarket_condition_id": "condition",
            "polymarket_yes_token_id": "yes-token",
            "polymarket_no_token_id": "no-token",
        }
        cutoff = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
        common = {
            "timestamp": int(cutoff.timestamp()),
            "tx_hash": "tx-1",
            "shares": 10_000_000,
            "shares_normalized": 10.0,
            "fee_usd": 0,
        }
        maker = pipeline.normalize_polymarket_trade(
            pair,
            {
                **common,
                "token_id": "yes-token",
                "outcome_label": "Yes",
                "is_yes_side": True,
                "side": "BUY",
                "price": 0.60,
                "amount_usd": 6.0,
                "order_hash": "maker-order",
                "user": "maker",
                "taker": "aggressor",
            },
            cutoff,
        )
        taker = pipeline.normalize_polymarket_trade(
            pair,
            {
                **common,
                "token_id": "no-token",
                "outcome_label": "No",
                "is_yes_side": False,
                "side": "BUY",
                "price": 0.40,
                "amount_usd": 4.0,
                "order_hash": "taker-order",
                "user": "aggressor",
                "taker": "exchange",
            },
            cutoff,
        )
        rows = pipeline.collapse_polymarket_transaction_fills(
            [maker, taker], pair["pair_id"]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_id"], "tx-1")
        self.assertEqual(rows[0]["record_unit"], "onchain_transaction")
        self.assertEqual(rows[0]["transaction_fill_count"], 2)
        self.assertAlmostEqual(rows[0]["canonical_yes_price"], 0.60)
        self.assertAlmostEqual(rows[0]["notional_usd"], 6.0)
        self.assertAlmostEqual(rows[0]["raw_token_notional_usd"], 4.0)

    def test_bbo_excludes_boundary_levels_and_separates_depth(self) -> None:
        pair = {
            "pair_id": "TEST",
            "event_key": "test",
            "family": "politics",
            "group_id": "group",
            "canonical_outcome": "Candidate",
            "analysis_tier": "primary",
            "polymarket_condition_id": "condition",
        }
        cutoff = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
        row = pipeline.normalize_snapshot(
            pair,
            "kalshi",
            "ticker",
            {
                "timestamp": int(cutoff.timestamp() * 1000),
                "yes_bids": [
                    {"price": 0, "size": 100},
                    {"price": "36.50", "size": "4.25"},
                    {"price": "36.50", "size": "1.75"},
                    {"price": "35.00", "size": "3"},
                ],
                "yes_asks": [
                    {"price": "37.00", "size": "2"},
                    {"price": 100, "size": 100},
                ],
            },
            cutoff,
        )
        self.assertAlmostEqual(row["best_bid"], 0.365)
        self.assertAlmostEqual(row["best_ask"], 0.37)
        self.assertAlmostEqual(row["best_bid_depth_contracts"], 6.0)
        self.assertAlmostEqual(row["full_bid_depth_contracts"], 9.0)
        self.assertFalse(row["treatment_received"])

    def test_run_log_does_not_write_api_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            event_root = Path(temporary)
            log = pipeline.RunLog(event_root, "test")
            log.progress(1, 2, "rows=3")
            log_path = log.path
            log.close()
            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Progress 1/2", contents)
            self.assertNotIn("x-api-key", contents.lower())


if __name__ == "__main__":
    unittest.main()
