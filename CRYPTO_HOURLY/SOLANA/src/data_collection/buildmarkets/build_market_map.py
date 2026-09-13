"""Build the exact SOL hourly time-and-strike market registry."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _pipeline_common import (
    ASSET_ROOT,
    RunLog,
    TREATMENT_TIME,
    WINDOW_START,
    iso_utc,
    parse_utc,
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
OUT_PATH = ASSET_ROOT / "data/matched_markets/SOL_market_map.csv"
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
    "strike",
    "strike_direction",
    "comparison_start_utc",
    "comparison_end_utc",
    "polymarket_condition_id",
    "polymarket_market_id",
    "polymarket_market_slug",
    "polymarket_event_slug",
    "polymarket_title",
    "polymarket_description",
    "polymarket_resolution_source",
    "polymarket_yes_token_id",
    "polymarket_no_token_id",
    "polymarket_yes_predexon_id",
    "polymarket_no_predexon_id",
    "kalshi_ticker",
    "kalshi_event_ticker",
    "kalshi_market_id",
    "kalshi_title",
    "kalshi_yes_subtitle",
    "kalshi_no_subtitle",
]


def market_key(row: dict[str, str]) -> tuple[str, Decimal]:
    return row["expiration_time_utc"], Decimal(row["strike"])


def unique_index(
    rows: list[dict[str, str]], venue: str
) -> dict[tuple[str, Decimal], dict[str, str]]:
    result: dict[tuple[str, Decimal], dict[str, str]] = {}
    for row in rows:
        key = market_key(row)
        if key in result:
            raise RuntimeError(f"Duplicate {venue} expiration/strike: {key}")
        result[key] = row
    return result


def main(run_log: RunLog) -> None:
    poly_rows = read_csv(POLY_PATH)
    kalshi_rows = read_csv(KALSHI_PATH)
    run_log.info(
        f"Matching configured family=hourly_fixed_strike asset=SOL "
        f"polymarket_candidates={len(poly_rows)} kalshi_candidates={len(kalshi_rows)}"
    )
    poly_by_key = unique_index(poly_rows, "Polymarket")
    kalshi_by_key = unique_index(kalshi_rows, "Kalshi")

    common_keys = sorted(set(poly_by_key) & set(kalshi_by_key))
    pairs: list[dict] = []
    for expiration_text, strike in common_keys:
        poly = poly_by_key[(expiration_text, strike)]
        kalshi = kalshi_by_key[(expiration_text, strike)]
        expiration = parse_utc(expiration_text)
        comparison_start = max(
            WINDOW_START,
            parse_utc(poly["listing_time_utc"]),
            parse_utc(kalshi["open_time_utc"]),
        )
        crosses = comparison_start < TREATMENT_TIME < expiration
        if expiration <= TREATMENT_TIME:
            period = "pre"
        elif comparison_start >= TREATMENT_TIME:
            period = "post"
        else:
            period = "transition"

        time_code = expiration.strftime("%Y%m%dT%H%MZ")
        strike_code = str(strike).replace(".", "p")
        pairs.append(
            {
                "pair_id": f"SOL_1H_{time_code}_S{strike_code}",
                "asset": "SOL",
                "family": "hourly_fixed_strike",
                "match_status": "exact_asset_expiration_strike_direction",
                "rule_validation_status": "native_rules_pending",
                "market_period": period,
                "crosses_treatment": crosses,
                "strike": str(strike),
                "strike_direction": "above",
                "comparison_start_utc": iso_utc(comparison_start),
                "comparison_end_utc": iso_utc(expiration),
                "polymarket_condition_id": poly["condition_id"],
                "polymarket_market_id": poly["market_id"],
                "polymarket_market_slug": poly["market_slug"],
                "polymarket_event_slug": poly["event_slug"],
                "polymarket_title": poly["title"],
                "polymarket_description": poly["description"],
                "polymarket_resolution_source": poly["resolution_source"],
                "polymarket_yes_token_id": poly["yes_token_id"],
                "polymarket_no_token_id": poly["no_token_id"],
                "polymarket_yes_predexon_id": poly["yes_predexon_id"],
                "polymarket_no_predexon_id": poly["no_predexon_id"],
                "kalshi_ticker": kalshi["ticker"],
                "kalshi_event_ticker": kalshi["event_ticker"],
                "kalshi_market_id": kalshi["market_id"],
                "kalshi_title": kalshi["title"],
                "kalshi_yes_subtitle": kalshi["yes_subtitle"],
                "kalshi_no_subtitle": kalshi["no_subtitle"],
            }
        )

    unmatched_poly = [
        row for key, row in poly_by_key.items() if key not in kalshi_by_key
    ]
    unmatched_kalshi = [
        row for key, row in kalshi_by_key.items() if key not in poly_by_key
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
            "matching_rule": "exact asset + expiration UTC + strike + above direction",
            "pairs": len(pairs),
            "transition_pairs": sum(row["market_period"] == "transition" for row in pairs),
            "unmatched_polymarket": len(unmatched_poly),
            "unmatched_kalshi": len(unmatched_kalshi),
            "native_rule_validation_pending": True,
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Matching output exact_pairs={len(pairs)} "
        f"transition_pairs={sum(row['market_period'] == 'transition' for row in pairs)} "
        f"unmatched_polymarket={len(unmatched_poly)} "
        f"unmatched_kalshi={len(unmatched_kalshi)} "
        f"market_map={OUT_PATH} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("build_market_map", main)
