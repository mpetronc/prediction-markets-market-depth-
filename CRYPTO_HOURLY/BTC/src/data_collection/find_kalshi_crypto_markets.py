"""Discover exact Kalshi counterparts for Polymarket BTC hourly strikes."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from _pipeline_common import (
    ASSET_ROOT,
    PredexonClient,
    RunLog,
    batched,
    iso_utc,
    parse_utc,
    read_csv,
    run_logged,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
)


POLY_PATH = (
    ASSET_ROOT
    / "data/matched_markets/polymarket/polymarket_candidate_markets.csv"
)
OUT_PATH = ASSET_ROOT / "data/matched_markets/kalshi/kalshi_candidate_markets.csv"
REJECTED_PATH = ASSET_ROOT / "data/matched_markets/kalshi/kalshi_rejected_markets.csv"
RAW_DIR = ASSET_ROOT / "data/raw/kalshi/discovery"
MANIFEST_PATH = ASSET_ROOT / "data/raw/kalshi/discovery_manifest.json"
NEW_YORK = ZoneInfo("America/New_York")

FIELDS = [
    "asset",
    "family",
    "ticker",
    "event_ticker",
    "series_ticker",
    "market_id",
    "title",
    "yes_subtitle",
    "no_subtitle",
    "status",
    "result",
    "open_time_utc",
    "expiration_time_utc",
    "expected_expiration_time_utc",
    "settlement_time_utc",
    "determination_time_utc",
    "strike",
    "strike_direction",
    "last_price",
    "volume",
    "open_interest",
    "dollar_volume",
    "dollar_open_interest",
    "source_polymarket_condition_id",
]


def kalshi_event_ticker(expiration_utc: str) -> str:
    local_expiration = parse_utc(expiration_utc).astimezone(NEW_YORK)
    return f"KXBTCD-{local_expiration.strftime('%y%b%d%H').upper()}"


def kalshi_market_ticker(expiration_utc: str, strike_text: str) -> str:
    strike = Decimal(strike_text)
    # Kalshi represents "$X or above" with a strict threshold at X - $0.01.
    threshold = strike - Decimal("0.01")
    return f"{kalshi_event_ticker(expiration_utc)}-T{threshold:.2f}"


def parse_kalshi_strike(value: object) -> Decimal | None:
    match = re.search(r"\$?([0-9][0-9,]*(?:\.[0-9]+)?)\s+or\s+above", str(value), re.I)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def optional_iso(value: object) -> str:
    return iso_utc(value) if value else ""


def normalize_market(
    market: dict,
    expected: dict[str, dict[str, str]],
) -> tuple[dict | None, str]:
    ticker = str(market.get("ticker") or "")
    source = expected.get(ticker)
    if source is None:
        return None, "ticker was not requested from a Polymarket candidate"

    event = market.get("event") or {}
    strike = parse_kalshi_strike(market.get("yes_subtitle"))
    try:
        expiration = parse_utc(market["close_time"])
        open_time = parse_utc(market["open_time"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid market timestamp: {exc}"

    reasons: list[str] = []
    if event.get("series_ticker") != "KXBTCD":
        reasons.append("wrong Kalshi series")
    if str(market.get("strike_type") or "").lower() != "greater":
        reasons.append("strike type is not greater")
    if strike is None or strike != Decimal(source["strike"]):
        reasons.append("displayed strike does not equal Polymarket strike")
    if iso_utc(expiration) != source["expiration_time_utc"]:
        reasons.append("expiration does not equal Polymarket expiration")

    row = {
        "asset": "BTC",
        "family": "hourly_fixed_strike",
        "ticker": ticker,
        "event_ticker": market.get("event_ticker"),
        "series_ticker": event.get("series_ticker"),
        "market_id": market.get("market_id"),
        "title": market.get("title"),
        "yes_subtitle": market.get("yes_subtitle"),
        "no_subtitle": market.get("no_subtitle"),
        "status": market.get("status"),
        "result": market.get("result"),
        "open_time_utc": iso_utc(open_time),
        "expiration_time_utc": iso_utc(expiration),
        "expected_expiration_time_utc": optional_iso(
            market.get("expected_expiration_time")
        ),
        "settlement_time_utc": optional_iso(market.get("settlement_time")),
        "determination_time_utc": optional_iso(market.get("determination_time")),
        "strike": str(strike) if strike is not None else "",
        "strike_direction": "above",
        "last_price": market.get("last_price"),
        "volume": market.get("volume"),
        "open_interest": market.get("open_interest"),
        "dollar_volume": market.get("dollar_volume"),
        "dollar_open_interest": market.get("dollar_open_interest"),
        "source_polymarket_condition_id": source["condition_id"],
    }
    return (None, "; ".join(reasons)) if reasons else (row, "")


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50:
        raise ValueError("--batch-size must be between 1 and 50")

    poly_rows = read_csv(POLY_PATH)
    expected: dict[str, dict[str, str]] = {}
    for row in poly_rows:
        ticker = kalshi_market_ticker(row["expiration_time_utc"], row["strike"])
        if ticker in expected:
            raise RuntimeError(f"Duplicate expected hourly ticker: {ticker}")
        expected[ticker] = row
    if args.max_markets > 0:
        expected = dict(list(expected.items())[: args.max_markets])

    client = PredexonClient(request_interval=args.request_interval, run_log=run_log)
    accepted: list[dict] = []
    rejected: list[dict] = []
    raw_page_number = 0

    failed_batches: list[dict] = []
    run_log.info(
        "Discovery configured venue=kalshi family=hourly_fixed_strike asset=BTC "
        f"requested_tickers={len(expected)} batch_size={args.batch_size} "
        f"source_candidates={POLY_PATH}"
    )

    def fetch_batch(ticker_batch: list[str]) -> None:
        nonlocal raw_page_number
        params: list[tuple[str, object]] = [("limit", 100)]
        params.extend(("ticker", ticker) for ticker in ticker_batch)
        try:
            for _, payload in client.cursor_pages("kalshi/markets", params, "markets"):
                raw_page_number += 1
                write_json_gzip_atomic(
                    payload, RAW_DIR / f"page_{raw_page_number:04d}.json.gz"
                )
                for market in payload["markets"]:
                    row, reason = normalize_market(market, expected)
                    if row is not None:
                        accepted.append(row)
                    else:
                        rejected.append(
                            {
                                "ticker": market.get("ticker"),
                                "event_ticker": market.get("event_ticker"),
                                "title": market.get("title"),
                                "rejection_reason": reason,
                            }
                        )
        except RuntimeError as exc:
            if len(ticker_batch) > 1:
                run_log.warning(
                    f"Kalshi batch failed size={len(ticker_batch)}; splitting batch error={exc}"
                )
                midpoint = len(ticker_batch) // 2
                fetch_batch(ticker_batch[:midpoint])
                fetch_batch(ticker_batch[midpoint:])
            else:
                run_log.error(
                    f"Kalshi ticker request failed ticker={ticker_batch[0]} error={exc}"
                )
                failed_batches.append({"ticker": ticker_batch[0], "error": str(exc)})

    ticker_batches = list(batched(sorted(expected), args.batch_size))
    for batch_number, ticker_batch in enumerate(ticker_batches, start=1):
        fetch_batch(ticker_batch)
        run_log.progress(
            batch_number,
            len(ticker_batches),
            f"batch_tickers={len(ticker_batch)} accepted_total={len(accepted)} "
            f"rejected_total={len(rejected)} raw_pages={raw_page_number}",
        )

    by_ticker = {row["ticker"]: row for row in accepted}
    accepted = sorted(
        by_ticker.values(),
        key=lambda row: (row["expiration_time_utc"], Decimal(row["strike"])),
    )
    missing_tickers = sorted(set(expected) - set(by_ticker))

    write_csv_atomic(accepted, OUT_PATH, FIELDS)
    write_csv_atomic(
        rejected,
        REJECTED_PATH,
        ["ticker", "event_ticker", "title", "rejection_reason"],
    )
    write_json_atomic(
        {
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "endpoint": "/v2/kalshi/markets",
            "series_ticker": "KXBTCD",
            "matching_candidate_rule": (
                "derive ticker from Polymarket expiration in America/New_York "
                "and threshold=strike-0.01"
            ),
            "requested_tickers": len(expected),
            "partial_run": args.max_markets > 0,
            "accepted_markets": len(accepted),
            "rejected_markets": len(rejected),
            "missing_tickers": missing_tickers,
            "failed_requests": failed_batches,
            "raw_pages": raw_page_number,
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Discovery output accepted={len(accepted)} rejected={len(rejected)} "
        f"missing={len(missing_tickers)} failed_requests={len(failed_batches)} "
        f"pages={raw_page_number} requests={client.request_count} retries={client.retry_count} "
        f"candidates_file={OUT_PATH} rejected_file={REJECTED_PATH} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("find_kalshi_crypto_markets", main)
