#!/usr/bin/env python3
"""Build the matched 2026 New York U.S. House market configuration.

The study follows the Democratic Party outcome in all 26 New York districts on
both Kalshi and Polymarket. Market identifiers are discovered from the public
platform APIs and validated before any output files are replaced.
"""

from __future__ import annotations

import argparse
import csv
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:  # The system trust store is sufficient in most environments.
    certifi = None


KALSHI_EVENT_URL = (
    "https://api.elections.kalshi.com/trade-api/v2/events/"
    "{event_ticker}?with_nested_markets=true"
)
POLYMARKET_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATCHED_DIR = PROJECT_ROOT / "data" / "matched_markets"
KALSHI_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "kalshi"
POLYMARKET_RAW_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket"

MARKET_MAP_PATH = MATCHED_DIR / "ny_house_market_map.csv"
CONFIG_PATH = MATCHED_DIR / "ny_house_2026_config.json"
KALSHI_RAW_PATH = KALSHI_RAW_DIR / "ny_house_events.json"
POLYMARKET_RAW_PATH = POLYMARKET_RAW_DIR / "ny_house_events.json"

ELECTION_DATE = "2026-11-03"
SELECTED_OUTCOME = "Democratic Party"
MAIN_WINDOW_START_UTC = "2026-11-03T11:00:00Z"
MAIN_WINDOW_END_UTC = "2026-11-04T02:00:00Z"
ROBUSTNESS_WINDOW_END_UTC = "2026-11-04T11:00:00Z"

SSL_CONTEXT = (
    ssl.create_default_context(cafile=certifi.where())
    if certifi is not None
    else ssl.create_default_context()
)

# Six districts still use Kalshi's older event naming scheme. The other twenty
# use the current KXHOUSERACE series.
LEGACY_KALSHI_EVENTS = {
    3: "HOUSENY3-26",
    4: "HOUSENY4-26",
    17: "HOUSENY17-26",
    18: "HOUSENY18-26",
    19: "HOUSENY19-26",
    22: "HOUSENY22-26",
}

CSV_FIELDS = [
    "politics_id",
    "state",
    "district",
    "election_date",
    "selected_outcome",
    "window_start_utc",
    "window_end_utc",
    "robustness_end_utc",
    "kalshi_event_ticker",
    "kalshi_democratic_ticker",
    "kalshi_republican_ticker",
    "kalshi_democratic_volume",
    "kalshi_republican_volume",
    "kalshi_rules_democratic",
    "kalshi_rules_republican",
    "polymarket_event_id",
    "polymarket_event_slug",
    "polymarket_democratic_market_id",
    "polymarket_democratic_condition_id",
    "polymarket_democratic_question_id",
    "polymarket_democratic_yes_token_id",
    "polymarket_democratic_no_token_id",
    "polymarket_democratic_volume",
    "polymarket_democratic_liquidity",
    "polymarket_republican_market_id",
    "polymarket_republican_condition_id",
    "polymarket_republican_question_id",
    "polymarket_republican_yes_token_id",
    "polymarket_republican_no_token_id",
    "polymarket_republican_volume",
    "polymarket_republican_liquidity",
    "polymarket_resolution_source",
    "polymarket_description",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of attempts for each API request (default: 3).",
    )
    return parser.parse_args()


def get_json(url: str, retries: int) -> Any:
    """Fetch and decode JSON, retrying temporary network/server failures."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ny-house-market-map/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=SSL_CONTEXT
            ) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def as_list(value: Any) -> list[Any]:
    """Normalize API fields that may be returned as arrays or JSON strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return decoded
    raise ValueError(f"Expected an array or JSON-encoded array, received: {value!r}")


def kalshi_event_ticker(district: int) -> str:
    return LEGACY_KALSHI_EVENTS.get(district, f"KXHOUSERACE-NY{district:02d}-26")


def find_party_market(markets: list[dict[str, Any]], party_code: str) -> dict[str, Any]:
    expected_suffix = f"-{party_code}"
    matches = [
        market
        for market in markets
        if str(market.get("ticker", "")).upper().endswith(expected_suffix)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one Kalshi {party_code} market; found {len(matches)}"
        )
    return matches[0]


def find_polymarket_party_market(
    markets: list[dict[str, Any]], party_name: str
) -> dict[str, Any]:
    question_fragment = f"Will the {party_name} win"
    matches = [
        market
        for market in markets
        if question_fragment.lower() in str(market.get("question", "")).lower()
        and market.get("active") is not False
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one active Polymarket {party_name} market; found {len(matches)}"
        )
    return matches[0]


def find_polymarket_binary_tokens(market: dict[str, Any]) -> tuple[str, str]:
    outcomes = [str(outcome).strip() for outcome in as_list(market.get("outcomes"))]
    token_ids = [str(token_id) for token_id in as_list(market.get("clobTokenIds"))]
    if len(outcomes) != len(token_ids):
        raise ValueError("Polymarket outcomes and token IDs have different lengths")

    token_by_outcome = dict(zip(outcomes, token_ids))
    try:
        return token_by_outcome["Yes"], token_by_outcome["No"]
    except KeyError as error:
        raise ValueError(
            f"Required Polymarket Yes/No outcome missing; received {outcomes}"
        ) from error


def build_row(
    district: int,
    kalshi_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
) -> dict[str, Any]:
    event_ticker = kalshi_event_ticker(district)
    kalshi_event = kalshi_payload.get("event", kalshi_payload)
    kalshi_markets = kalshi_event.get("markets") or kalshi_payload.get("markets") or []
    if not isinstance(kalshi_markets, list):
        raise ValueError(f"Kalshi markets are missing for {event_ticker}")

    democratic_market = find_party_market(kalshi_markets, "D")
    republican_market = find_party_market(kalshi_markets, "R")

    polymarket_events = (
        polymarket_payload if isinstance(polymarket_payload, list) else [polymarket_payload]
    )
    if len(polymarket_events) != 1:
        raise ValueError(
            f"Expected one Polymarket event for district {district}; "
            f"found {len(polymarket_events)}"
        )
    polymarket_event = polymarket_events[0]
    polymarket_markets = polymarket_event.get("markets") or []
    if not isinstance(polymarket_markets, list):
        raise ValueError(f"Polymarket markets are missing for district {district}")
    polymarket_democratic = find_polymarket_party_market(
        polymarket_markets, "Democratic Party"
    )
    polymarket_republican = find_polymarket_party_market(
        polymarket_markets, "Republican Party"
    )
    democratic_yes_token, democratic_no_token = find_polymarket_binary_tokens(
        polymarket_democratic
    )
    republican_yes_token, republican_no_token = find_polymarket_binary_tokens(
        polymarket_republican
    )

    return {
        "politics_id": f"NY_HOUSE_{district:02d}",
        "state": "NY",
        "district": district,
        "election_date": ELECTION_DATE,
        "selected_outcome": SELECTED_OUTCOME,
        "window_start_utc": MAIN_WINDOW_START_UTC,
        "window_end_utc": MAIN_WINDOW_END_UTC,
        "robustness_end_utc": ROBUSTNESS_WINDOW_END_UTC,
        "kalshi_event_ticker": event_ticker,
        "kalshi_democratic_ticker": democratic_market.get("ticker", ""),
        "kalshi_republican_ticker": republican_market.get("ticker", ""),
        "kalshi_democratic_volume": democratic_market.get("volume", ""),
        "kalshi_republican_volume": republican_market.get("volume", ""),
        "kalshi_rules_democratic": democratic_market.get("rules_primary", ""),
        "kalshi_rules_republican": republican_market.get("rules_primary", ""),
        "polymarket_event_id": polymarket_event.get("id", ""),
        "polymarket_event_slug": polymarket_event.get("slug", ""),
        "polymarket_democratic_market_id": polymarket_democratic.get("id", ""),
        "polymarket_democratic_condition_id": polymarket_democratic.get(
            "conditionId", ""
        ),
        "polymarket_democratic_question_id": polymarket_democratic.get(
            "questionID", ""
        ),
        "polymarket_democratic_yes_token_id": democratic_yes_token,
        "polymarket_democratic_no_token_id": democratic_no_token,
        "polymarket_democratic_volume": polymarket_democratic.get("volume", ""),
        "polymarket_democratic_liquidity": polymarket_democratic.get(
            "liquidity", ""
        ),
        "polymarket_republican_market_id": polymarket_republican.get("id", ""),
        "polymarket_republican_condition_id": polymarket_republican.get(
            "conditionId", ""
        ),
        "polymarket_republican_question_id": polymarket_republican.get(
            "questionID", ""
        ),
        "polymarket_republican_yes_token_id": republican_yes_token,
        "polymarket_republican_no_token_id": republican_no_token,
        "polymarket_republican_volume": polymarket_republican.get("volume", ""),
        "polymarket_republican_liquidity": polymarket_republican.get(
            "liquidity", ""
        ),
        "polymarket_resolution_source": polymarket_democratic.get(
            "resolutionSource", ""
        ),
        "polymarket_description": polymarket_democratic.get("description", ""),
    }


def validate_rows(rows: list[dict[str, Any]]) -> None:
    expected_districts = list(range(1, 27))
    actual_districts = [int(row["district"]) for row in rows]
    if actual_districts != expected_districts:
        raise ValueError(f"District coverage is invalid: {actual_districts}")

    required_fields = [
        "kalshi_democratic_ticker",
        "kalshi_republican_ticker",
        "polymarket_event_id",
        "polymarket_democratic_market_id",
        "polymarket_democratic_condition_id",
        "polymarket_democratic_yes_token_id",
        "polymarket_democratic_no_token_id",
        "polymarket_republican_market_id",
        "polymarket_republican_condition_id",
        "polymarket_republican_yes_token_id",
        "polymarket_republican_no_token_id",
    ]
    for row in rows:
        missing = [field for field in required_fields if not str(row.get(field, "")).strip()]
        if missing:
            raise ValueError(f"{row['politics_id']} is missing {missing}")

    unique_fields = [
        "kalshi_democratic_ticker",
        "kalshi_republican_ticker",
        "polymarket_democratic_condition_id",
        "polymarket_democratic_yes_token_id",
        "polymarket_democratic_no_token_id",
        "polymarket_republican_condition_id",
        "polymarket_republican_yes_token_id",
        "polymarket_republican_no_token_id",
    ]
    for field in unique_fields:
        values = [str(row[field]) for row in rows]
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate values found in {field}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def build_config(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "project_id": "ny_house_2026",
        "title": "2026 New York U.S. House elections",
        "state": "NY",
        "chamber": "U.S. House of Representatives",
        "election_date": ELECTION_DATE,
        "selected_outcome": SELECTED_OUTCOME,
        "district_count": len(rows),
        "districts": [int(row["district"]) for row in rows],
        "main_window": {
            "start_utc": MAIN_WINDOW_START_UTC,
            "end_utc": MAIN_WINDOW_END_UTC,
            "description": (
                "Election Day from 06:00 to 21:00 America/New_York "
                "(poll-opening through poll-closing time)."
            ),
        },
        "robustness_window": {
            "start_utc": MAIN_WINDOW_START_UTC,
            "end_utc": ROBUSTNESS_WINDOW_END_UTC,
            "description": "Election Day 06:00 through the next day 06:00 New York time.",
        },
        "market_map": "data/matched_markets/ny_house_market_map.csv",
        "raw_snapshots": {
            "kalshi": "data/raw/kalshi/ny_house_events.json",
            "polymarket": "data/raw/polymarket/ny_house_events.json",
        },
        "resolution_caveat": (
            "The platforms are directionally matched but not legally identical: "
            "Polymarket asks which party wins the election, while Kalshi's rules "
            "refer to the party of the member sworn in for the term beginning in 2027."
        ),
        "markets": [
            {
                "politics_id": row["politics_id"],
                "district": int(row["district"]),
                "kalshi_event_ticker": row["kalshi_event_ticker"],
                "kalshi_selected_ticker": row["kalshi_democratic_ticker"],
                "polymarket_event_slug": row["polymarket_event_slug"],
                "polymarket_selected_market_id": row[
                    "polymarket_democratic_market_id"
                ],
                "polymarket_selected_condition_id": row[
                    "polymarket_democratic_condition_id"
                ],
                "polymarket_selected_yes_token_id": row[
                    "polymarket_democratic_yes_token_id"
                ],
                "polymarket_selected_no_token_id": row[
                    "polymarket_democratic_no_token_id"
                ],
            }
            for row in rows
        ],
    }


def main() -> None:
    args = parse_args()
    if args.retries < 1:
        raise ValueError("--retries must be at least 1")

    rows: list[dict[str, Any]] = []
    kalshi_raw: dict[str, Any] = {}
    polymarket_raw: dict[str, Any] = {}

    for district in range(1, 27):
        event_ticker = kalshi_event_ticker(district)
        event_slug = f"ny-{district:02d}-house-election-winner"
        print(f"Fetching NY-{district:02d}: {event_ticker} / {event_slug}")

        kalshi_payload = get_json(
            KALSHI_EVENT_URL.format(event_ticker=event_ticker), args.retries
        )
        polymarket_payload = get_json(
            POLYMARKET_EVENT_URL.format(slug=event_slug), args.retries
        )
        kalshi_raw[event_ticker] = kalshi_payload
        polymarket_raw[event_slug] = polymarket_payload
        rows.append(build_row(district, kalshi_payload, polymarket_payload))

    validate_rows(rows)
    write_csv(MARKET_MAP_PATH, rows)
    write_json(CONFIG_PATH, build_config(rows))
    write_json(KALSHI_RAW_PATH, kalshi_raw)
    write_json(POLYMARKET_RAW_PATH, polymarket_raw)

    print(f"Validated {len(rows)} matched district markets.")
    print(f"Market map: {MARKET_MAP_PATH}")
    print(f"Configuration: {CONFIG_PATH}")


if __name__ == "__main__":
    main()
