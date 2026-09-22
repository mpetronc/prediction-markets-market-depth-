"""Discover ETH 15-minute Up/Down markets from Predexon."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone

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
    "market_slug",
    "event_slug",
    "title",
    "status",
    "winning_side",
    "listing_time_utc",
    "interval_start_utc",
    "interval_end_utc",
    "market_period",
    "crosses_treatment",
    "up_token_id",
    "down_token_id",
    "up_price",
    "down_price",
    "total_volume_usd",
    "liquidity_usd",
]


def classify_period(start: datetime, end: datetime) -> tuple[str, bool]:
    crosses = start < TREATMENT_TIME < end
    if end <= TREATMENT_TIME:
        return "pre", crosses
    if start >= TREATMENT_TIME:
        return "post", crosses
    return "transition", True


def normalize_market(market: dict) -> tuple[dict | None, str]:
    try:
        end = parse_utc(market["end_time"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid end_time: {exc}"

    # Predexon's start_time is the listing time for this endpoint. The actual
    # reference interval begins 15 minutes before end_time.
    interval_start = end - timedelta(minutes=15)
    slug = str(market.get("market_slug") or "")
    slug_match = re.search(r"-(\d{10})$", slug)
    slug_start = (
        datetime.fromtimestamp(int(slug_match.group(1)), tz=timezone.utc)
        if slug_match
        else None
    )

    reasons: list[str] = []
    if str(market.get("asset", "")).lower() != "eth":
        reasons.append("asset is not eth")
    if str(market.get("timeframe", "")).lower() != "15m":
        reasons.append("timeframe is not 15m")
    if not (WINDOW_START < end <= WINDOW_END):
        reasons.append("end_time outside requested window")
    if slug_start and slug_start != interval_start:
        reasons.append("slug interval start differs from end_time minus 15 minutes")
    if not market.get("condition_id"):
        reasons.append("missing condition_id")
    if not market.get("up_token_id") or not market.get("down_token_id"):
        reasons.append("missing outcome token")

    period, crosses = classify_period(interval_start, end)
    row = {
        "asset": "ETH",
        "family": "up_down_15m",
        "condition_id": market.get("condition_id"),
        "market_slug": slug,
        "event_slug": market.get("event_slug"),
        "title": market.get("title"),
        "status": market.get("status"),
        "winning_side": market.get("winning_side"),
        "listing_time_utc": iso_utc(market.get("start_time")),
        "interval_start_utc": iso_utc(interval_start),
        "interval_end_utc": iso_utc(end),
        "market_period": period,
        "crosses_treatment": crosses,
        "up_token_id": market.get("up_token_id"),
        "down_token_id": market.get("down_token_id"),
        "up_price": market.get("up_price"),
        "down_price": market.get("down_price"),
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
        "asset": "eth",
        "timeframe": "15m",
        "status": "closed",
        "end_after": epoch_seconds(WINDOW_START),
        "end_before": epoch_seconds(WINDOW_END),
        "sort": "asc",
        "limit": 200,
    }
    run_log.info(
        "Discovery configured venue=polymarket family=up_down_15m asset=ETH "
        f"window={iso_utc(WINDOW_START)}..{iso_utc(WINDOW_END)} endpoint=/v2/polymarket/crypto-updown"
    )

    accepted: list[dict] = []
    rejected: list[dict] = []
    pages = 0
    for page_number, payload in client.offset_pages(
        "polymarket/crypto-updown", params, "markets"
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

    accepted.sort(key=lambda row: row["interval_end_utc"])
    write_csv_atomic(accepted, OUT_PATH, FIELDS)
    write_csv_atomic(
        rejected,
        REJECTED_PATH,
        ["condition_id", "market_slug", "title", "rejection_reason"],
    )
    write_json_atomic(
        {
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "endpoint": "/v2/polymarket/crypto-updown",
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
