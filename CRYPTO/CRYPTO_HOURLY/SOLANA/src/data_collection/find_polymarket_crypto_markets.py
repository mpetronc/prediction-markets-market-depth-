"""Discover SOL hourly fixed-strike markets from Predexon."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from _pipeline_common import (
    ASSET_ROOT,
    TREATMENT_TIME,
    WINDOW_END,
    WINDOW_START,
    PredexonClient,
    RunLog,
    epoch_seconds,
    iso_utc,
    parse_utc,
    run_logged,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
)


OUT_PATH = (
    ASSET_ROOT
    / "data/matched_markets/polymarket/polymarket_candidate_markets.csv"
)
REJECTED_PATH = (
    ASSET_ROOT
    / "data/matched_markets/polymarket/polymarket_rejected_markets.csv"
)
RAW_DIR = ASSET_ROOT / "data/raw/polymarket/discovery"
MANIFEST_PATH = ASSET_ROOT / "data/raw/polymarket/discovery_manifest.json"

FIELDS = [
    "asset",
    "family",
    "condition_id",
    "market_id",
    "market_slug",
    "event_id",
    "event_slug",
    "event_title",
    "title",
    "description",
    "status",
    "winning_side",
    "listing_time_utc",
    "expiration_time_utc",
    "close_time_utc",
    "market_period",
    "strike",
    "strike_direction",
    "yes_token_id",
    "no_token_id",
    "yes_predexon_id",
    "no_predexon_id",
    "resolution_source",
    "total_volume_usd",
    "liquidity_usd",
]


def parse_strike(title: object) -> Decimal | None:
    match = re.search(
        r"solana\s+above\s+\$?([0-9][0-9,]*(?:\.[0-9]+)?)",
        str(title),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def outcome_by_label(market: dict, label: str) -> dict:
    for outcome in market.get("outcomes") or []:
        if str(outcome.get("label", "")).lower() == label.lower():
            return outcome
    return {}


def optional_iso(value: object) -> str:
    return iso_utc(value) if value else ""


def normalize_market(market: dict) -> tuple[dict | None, str]:
    try:
        listing_time = parse_utc(market["start_time"])
        expiration = parse_utc(market["end_time"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid market timestamp: {exc}"

    title = str(market.get("title") or "")
    description = str(market.get("description") or "")
    tags = {str(tag).lower() for tag in market.get("tags") or []}
    strike = parse_strike(title)
    yes = outcome_by_label(market, "Yes")
    no = outcome_by_label(market, "No")

    reasons: list[str] = []
    if strike is None:
        reasons.append("could not parse an absolute strike")
    if "1h" not in tags or "multi strikes" not in tags:
        reasons.append("missing 1H/Multi Strikes tags")
    if "sol/usdt 1 hour candle" not in description.lower():
        reasons.append("resolution is not the SOL/USDT 1-hour close")
    if expiration.minute != 0 or expiration.second != 0:
        reasons.append("expiration is not on an exact hour")
    if not (WINDOW_START < expiration <= WINDOW_END):
        reasons.append("expiration outside requested window")
    if not market.get("condition_id"):
        reasons.append("missing condition_id")
    if not yes.get("token_id") or not no.get("token_id"):
        reasons.append("missing Yes/No token")

    row = {
        "asset": "SOL",
        "family": "hourly_fixed_strike",
        "condition_id": market.get("condition_id"),
        "market_id": market.get("market_id"),
        "market_slug": market.get("market_slug"),
        "event_id": market.get("event_id"),
        "event_slug": market.get("event_slug"),
        "event_title": market.get("event_title"),
        "title": title,
        "description": description,
        "status": market.get("status"),
        "winning_side": market.get("winning_side"),
        "listing_time_utc": iso_utc(listing_time),
        "expiration_time_utc": iso_utc(expiration),
        "close_time_utc": optional_iso(market.get("close_time")),
        "market_period": "pre" if expiration <= TREATMENT_TIME else "post",
        "strike": str(strike) if strike is not None else "",
        "strike_direction": "above",
        "yes_token_id": yes.get("token_id"),
        "no_token_id": no.get("token_id"),
        "yes_predexon_id": yes.get("predexon_id"),
        "no_predexon_id": no.get("predexon_id"),
        "resolution_source": "Binance SOL/USDT 1h close",
        "total_volume_usd": market.get("total_volume_usd"),
        "liquidity_usd": market.get("liquidity_usd"),
    }
    return (None, "; ".join(reasons)) if reasons else (row, "")


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-interval", type=float, default=None)
    args = parser.parse_args()

    client = PredexonClient(request_interval=args.request_interval, run_log=run_log)
    params = {
        "status": "closed",
        "search": "Solana above",
        "end_after": epoch_seconds(WINDOW_START),
        "end_before": epoch_seconds(WINDOW_END),
        "sort": "expiration_asc",
        "limit": 100,
    }
    run_log.info(
        "Discovery configured venue=polymarket family=hourly_fixed_strike asset=SOL "
        f"window={iso_utc(WINDOW_START)}..{iso_utc(WINDOW_END)} endpoint=/v2/polymarket/markets"
    )

    accepted: list[dict] = []
    rejected: list[dict] = []
    pages = 0
    for page_number, payload in client.cursor_pages(
        "polymarket/markets", params, "markets"
    ):
        pages = page_number
        write_json_gzip_atomic(
            payload, RAW_DIR / f"page_{page_number:04d}.json.gz"
        )
        for market in payload["markets"]:
            row, reason = normalize_market(market)
            if row is not None:
                accepted.append(row)
            else:
                rejected.append(
                    {
                        "condition_id": market.get("condition_id"),
                        "market_slug": market.get("market_slug"),
                        "title": market.get("title"),
                        "rejection_reason": reason,
                    }
                )
        run_log.info(
            f"Discovery page={page_number} rows={len(payload['markets'])} "
            f"accepted_total={len(accepted)} rejected_total={len(rejected)}"
        )

    accepted.sort(key=lambda row: (row["expiration_time_utc"], Decimal(row["strike"])))
    write_csv_atomic(accepted, OUT_PATH, FIELDS)
    write_csv_atomic(
        rejected,
        REJECTED_PATH,
        ["condition_id", "market_slug", "title", "rejection_reason"],
    )
    write_json_atomic(
        {
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "endpoint": "/v2/polymarket/markets",
            "parameters": params,
            "window_start_utc": iso_utc(WINDOW_START),
            "treatment_time_utc": iso_utc(TREATMENT_TIME),
            "window_end_utc": iso_utc(WINDOW_END),
            "pages": pages,
            "accepted_markets": len(accepted),
            "rejected_markets": len(rejected),
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Discovery output accepted={len(accepted)} rejected={len(rejected)} "
        f"pages={pages} requests={client.request_count} retries={client.retry_count} "
        f"candidates_file={OUT_PATH} rejected_file={REJECTED_PATH} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("find_polymarket_crypto_markets", main)
