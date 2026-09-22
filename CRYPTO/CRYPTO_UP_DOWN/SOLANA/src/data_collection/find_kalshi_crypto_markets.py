"""Discover the matching Kalshi SOL 15-minute markets from Predexon."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from _pipeline_common import (
    ASSET_ROOT,
    TREATMENT_TIME,
    WINDOW_END,
    WINDOW_START,
    PredexonClient,
    RunLog,
    batched,
    iso_utc,
    parse_utc,
    run_logged,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
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
    "interval_start_utc",
    "interval_end_utc",
    "expected_expiration_time_utc",
    "settlement_time_utc",
    "determination_time_utc",
    "market_period",
    "crosses_treatment",
    "last_price",
    "volume",
    "open_interest",
    "dollar_volume",
    "dollar_open_interest",
]


def expected_market_tickers() -> list[str]:
    result: list[str] = []
    end = WINDOW_START + timedelta(minutes=15)
    while end <= WINDOW_END:
        local_end = end.astimezone(NEW_YORK)
        suffix = local_end.strftime("%y%b%d%H%M").upper()
        result.append(f"KXSOL15M-{suffix}-{local_end.strftime('%M')}")
        end += timedelta(minutes=15)
    return result


def classify_period(start: datetime, end: datetime) -> tuple[str, bool]:
    crosses = start < TREATMENT_TIME < end
    if end <= TREATMENT_TIME:
        return "pre", crosses
    if start >= TREATMENT_TIME:
        return "post", crosses
    return "transition", True


def optional_iso(value: object) -> str:
    return iso_utc(value) if value else ""


def normalize_market(market: dict) -> tuple[dict | None, str]:
    try:
        start = parse_utc(market["open_time"])
        end = parse_utc(market["close_time"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid interval timestamp: {exc}"

    event = market.get("event") or {}
    reasons: list[str] = []
    if event.get("series_ticker") != "KXSOL15M":
        reasons.append("wrong series ticker")
    if end - start != timedelta(minutes=15):
        reasons.append("market is not exactly 15 minutes")
    if not (WINDOW_START < end <= WINDOW_END):
        reasons.append("close_time outside requested window")
    if not market.get("ticker"):
        reasons.append("missing ticker")

    period, crosses = classify_period(start, end)
    row = {
        "asset": "SOL",
        "family": "up_down_15m",
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "series_ticker": event.get("series_ticker"),
        "market_id": market.get("market_id"),
        "title": market.get("title"),
        "yes_subtitle": market.get("yes_subtitle"),
        "no_subtitle": market.get("no_subtitle"),
        "status": market.get("status"),
        "result": market.get("result"),
        "interval_start_utc": iso_utc(start),
        "interval_end_utc": iso_utc(end),
        "expected_expiration_time_utc": optional_iso(
            market.get("expected_expiration_time")
        ),
        "settlement_time_utc": optional_iso(market.get("settlement_time")),
        "determination_time_utc": optional_iso(market.get("determination_time")),
        "market_period": period,
        "crosses_treatment": crosses,
        "last_price": market.get("last_price"),
        "volume": market.get("volume"),
        "open_interest": market.get("open_interest"),
        "dollar_volume": market.get("dollar_volume"),
        "dollar_open_interest": market.get("dollar_open_interest"),
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

    client = PredexonClient(request_interval=args.request_interval, run_log=run_log)
    accepted: list[dict] = []
    rejected: list[dict] = []
    raw_page_number = 0

    expected = expected_market_tickers()
    if args.max_markets > 0:
        expected = expected[: args.max_markets]
    failed_batches: list[dict] = []
    run_log.info(
        "Discovery configured venue=kalshi family=up_down_15m asset=SOL "
        f"window={iso_utc(WINDOW_START)}..{iso_utc(WINDOW_END)} "
        f"requested_tickers={len(expected)} batch_size={args.batch_size}"
    )

    def fetch_batch(ticker_batch: list[str]) -> None:
        nonlocal raw_page_number
        params: list[tuple[str, object]] = [
            ("status", "closed"),
            ("limit", 100),
        ]
        params.extend(("ticker", ticker) for ticker in ticker_batch)
        try:
            for _, payload in client.cursor_pages("kalshi/markets", params, "markets"):
                raw_page_number += 1
                write_json_gzip_atomic(
                    payload, RAW_DIR / f"page_{raw_page_number:04d}.json.gz"
                )
                for market in payload["markets"]:
                    row, reason = normalize_market(market)
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
                failed_batches.append(
                    {"ticker": ticker_batch[0], "error": str(exc)}
                )

    ticker_batches = list(batched(expected, args.batch_size))
    for batch_number, ticker_batch in enumerate(ticker_batches, start=1):
        fetch_batch(ticker_batch)
        run_log.progress(
            batch_number,
            len(ticker_batches),
            f"batch_tickers={len(ticker_batch)} accepted_total={len(accepted)} "
            f"rejected_total={len(rejected)} raw_pages={raw_page_number}",
        )

    by_ticker = {row["ticker"]: row for row in accepted}
    accepted = sorted(by_ticker.values(), key=lambda row: row["interval_end_utc"])
    returned_tickers = {row["ticker"] for row in accepted}
    missing_tickers = sorted(set(expected) - returned_tickers)

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
            "series_ticker": "KXSOL15M",
            "window_start_utc": iso_utc(WINDOW_START),
            "treatment_time_utc": iso_utc(TREATMENT_TIME),
            "window_end_utc": iso_utc(WINDOW_END),
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
