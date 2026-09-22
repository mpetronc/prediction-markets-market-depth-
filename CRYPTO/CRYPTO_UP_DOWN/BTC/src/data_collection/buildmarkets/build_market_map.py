"""Build the exact BTC 15-minute Polymarket/Kalshi market registry."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _pipeline_common import (
    ASSET_ROOT,
    RunLog,
    read_csv,
    run_logged,
    write_csv_atomic,
    write_json_atomic,
)


POLY_PATH = (
    ASSET_ROOT
    / "data/matched_markets/polymarket/polymarket_candidate_markets.csv"
)
KALSHI_PATH = ASSET_ROOT / "data/matched_markets/kalshi/kalshi_candidate_markets.csv"
OUT_PATH = ASSET_ROOT / "data/matched_markets/BTC_market_map.csv"
UNMATCHED_POLY_PATH = (
    ASSET_ROOT / "data/matched_markets/polymarket/unmatched_polymarket_markets.csv"
)
UNMATCHED_KALSHI_PATH = (
    ASSET_ROOT / "data/matched_markets/kalshi/unmatched_kalshi_markets.csv"
)
MANIFEST_PATH = ASSET_ROOT / "data/matched_markets/market_map_manifest.json"

FIELDS = [
    "pair_id",
    "asset",
    "family",
    "match_status",
    "rule_validation_status",
    "market_period",
    "crosses_treatment",
    "comparison_start_utc",
    "comparison_end_utc",
    "polymarket_condition_id",
    "polymarket_market_slug",
    "polymarket_event_slug",
    "polymarket_title",
    "polymarket_up_token_id",
    "polymarket_down_token_id",
    "kalshi_ticker",
    "kalshi_event_ticker",
    "kalshi_market_id",
    "kalshi_title",
    "kalshi_yes_subtitle",
    "kalshi_no_subtitle",
]


def interval_key(row: dict[str, str]) -> tuple[str, str]:
    return row["interval_start_utc"], row["interval_end_utc"]


def unique_index(rows: list[dict[str, str]], venue: str) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = interval_key(row)
        if key in result:
            raise RuntimeError(f"Duplicate {venue} interval in candidate data: {key}")
        result[key] = row
    return result


def main(run_log: RunLog) -> None:
    poly_rows = read_csv(POLY_PATH)
    kalshi_rows = read_csv(KALSHI_PATH)
    run_log.info(
        f"Matching configured family=up_down_15m asset=BTC "
        f"polymarket_candidates={len(poly_rows)} kalshi_candidates={len(kalshi_rows)}"
    )
    poly_by_interval = unique_index(poly_rows, "Polymarket")
    kalshi_by_interval = unique_index(kalshi_rows, "Kalshi")

    common_keys = sorted(set(poly_by_interval) & set(kalshi_by_interval))
    pairs: list[dict] = []
    for start_utc, end_utc in common_keys:
        poly = poly_by_interval[(start_utc, end_utc)]
        kalshi = kalshi_by_interval[(start_utc, end_utc)]
        pair_time = start_utc.replace("-", "").replace(":", "")[:13]
        pairs.append(
            {
                "pair_id": f"BTC_15M_{pair_time}Z",
                "asset": "BTC",
                "family": "up_down_15m",
                "match_status": "exact_asset_and_interval",
                # Predexon metadata proves the time/payoff family but does not
                # include complete native oracle language for both venues.
                "rule_validation_status": "native_rules_pending",
                "market_period": poly["market_period"],
                "crosses_treatment": poly["crosses_treatment"],
                "comparison_start_utc": start_utc,
                "comparison_end_utc": end_utc,
                "polymarket_condition_id": poly["condition_id"],
                "polymarket_market_slug": poly["market_slug"],
                "polymarket_event_slug": poly["event_slug"],
                "polymarket_title": poly["title"],
                "polymarket_up_token_id": poly["up_token_id"],
                "polymarket_down_token_id": poly["down_token_id"],
                "kalshi_ticker": kalshi["ticker"],
                "kalshi_event_ticker": kalshi["event_ticker"],
                "kalshi_market_id": kalshi["market_id"],
                "kalshi_title": kalshi["title"],
                "kalshi_yes_subtitle": kalshi["yes_subtitle"],
                "kalshi_no_subtitle": kalshi["no_subtitle"],
            }
        )

    unmatched_poly = [
        row for key, row in poly_by_interval.items() if key not in kalshi_by_interval
    ]
    unmatched_kalshi = [
        row for key, row in kalshi_by_interval.items() if key not in poly_by_interval
    ]

    write_csv_atomic(pairs, OUT_PATH, FIELDS)
    write_csv_atomic(unmatched_poly, UNMATCHED_POLY_PATH, list(poly_rows[0]) if poly_rows else [])
    write_csv_atomic(
        unmatched_kalshi,
        UNMATCHED_KALSHI_PATH,
        list(kalshi_rows[0]) if kalshi_rows else [],
    )
    write_json_atomic(
        {
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "matching_rule": "exact asset + exact 15-minute UTC interval",
            "pairs": len(pairs),
            "unmatched_polymarket": len(unmatched_poly),
            "unmatched_kalshi": len(unmatched_kalshi),
            "native_rule_validation_pending": True,
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Matching output exact_pairs={len(pairs)} "
        f"unmatched_polymarket={len(unmatched_poly)} "
        f"unmatched_kalshi={len(unmatched_kalshi)} "
        f"market_map={OUT_PATH} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("build_market_map", main)
