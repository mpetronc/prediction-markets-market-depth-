"""Offline checks for the corrective native-Kalshi pipeline."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


DATA_COLLECTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_COLLECTION))

from fetch_kalshi_native_trades import (  # noqa: E402
    build_summary,
    native_trade_timestamp,
    normalize_native_trade,
    trade_segments,
)


class NativeKalshiPipelineTests(unittest.TestCase):
    def test_native_trade_preserves_count_fp_and_dollar_price(self) -> None:
        pair = {
            "pair_id": "SOL_1H_TEST",
            "asset": "SOL",
            "family": "hourly_fixed_strike",
            "kalshi_ticker": "KXSOLD-TEST",
        }
        trade = {
            "trade_id": "trade-1",
            "ticker": "KXSOLD-TEST",
            "count_fp": "13.79",
            "yes_price_dollars": "0.5300",
            "no_price_dollars": "0.4700",
            "taker_side": "no",
            "created_time": "2026-08-10T11:00:01.250Z",
        }

        row = normalize_native_trade(pair, trade)

        self.assertEqual(row["size"], "13.79")
        self.assertEqual(row["size_source"], "kalshi_native_count_fp")
        self.assertTrue(row["size_is_exact"])
        self.assertEqual(row["price"], "0.5300")
        self.assertEqual(row["notional_usd"], "7.308700")
        self.assertEqual(row["timestamp_ms"] % 1000, 250)

    def test_timestamp_parser_accepts_epoch_seconds(self) -> None:
        self.assertEqual(
            native_trade_timestamp({"created_time": 1786359601}),
            (1786359601, 1786359601000),
        )

    def test_trade_routing_before_after_and_across_cutoff(self) -> None:
        self.assertEqual(
            trade_segments(100, 200, 250),
            [("historical", "historical/trades", 100, 200)],
        )
        self.assertEqual(
            trade_segments(300, 400, 250),
            [("live", "markets/trades", 300, 400)],
        )
        self.assertEqual(
            trade_segments(200, 300, 250),
            [
                ("historical", "historical/trades", 200, 250),
                ("live", "markets/trades", 250, 300),
            ],
        )

    def test_summary_counts_affected_markets_and_periods(self) -> None:
        base = {
            "predexon_trades": 2,
            "matched_trade_ids": 2,
            "native_only_trade_ids": 0,
            "predexon_only_trade_ids": 0,
            "native_contracts": "10.50",
            "signed_quantity_error": "-0.50",
            "absolute_quantity_error": "0.50",
            "signed_price_error": "-0.0010",
            "absolute_price_error": "0.0010",
        }
        audits = [
            {
                **base,
                "observation_period": "pre",
                "native_trades": 2,
                "fractional_native_trades": 1,
                "subcent_native_trades": 1,
                "matched_quantity_discrepancies": 1,
                "matched_price_discrepancies": 1,
            },
            {
                **base,
                "observation_period": "post",
                "native_trades": 2,
                "fractional_native_trades": 0,
                "subcent_native_trades": 0,
                "matched_quantity_discrepancies": 0,
                "matched_price_discrepancies": 0,
            },
        ]

        summary = build_summary(audits)

        self.assertEqual(summary["overall"]["markets_with_fractional_trades"], 1)
        self.assertEqual(summary["overall"]["fractional_native_trades"], 1)
        self.assertEqual(summary["overall"]["markets_with_subcent_trades"], 1)
        self.assertEqual(summary["overall"]["matched_price_discrepancies"], 1)
        self.assertEqual(summary["by_period"]["pre"]["markets_with_fractional_trades"], 1)
        self.assertEqual(summary["by_period"]["post"]["markets_with_fractional_trades"], 0)


if __name__ == "__main__":
    unittest.main()


