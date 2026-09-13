"""Check Predexon-missing SOL 15-minute intervals against Kalshi directly."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from _kalshi_public import BASE_URL, KalshiPublicClient
from _pipeline_common import (
    ASSET_ROOT,
    RunLog,
    WINDOW_START,
    iso_utc,
    run_logged,
    treatment_period,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
)
from find_kalshi_crypto_markets import expected_market_tickers


PREDEXON_MANIFEST = ASSET_ROOT / "data/raw/kalshi/discovery_manifest.json"
PART_ROOT = ASSET_ROOT / "data/processed/_parts/missing_market_audit/kalshi_native"
RAW_ROOT = ASSET_ROOT / "data/raw/kalshi_native/missing_market_audit"
AUDIT_PATH = ASSET_ROOT / "data/audits/kalshi_missing_interval_audit.csv"
SUMMARY_PATH = ASSET_ROOT / "data/audits/kalshi_missing_interval_summary.json"
SCHEMA_VERSION = "2026-09-07.1"

FIELDS = [
    "schema_version",
    "ticker",
    "interval_start_utc",
    "interval_end_utc",
    "observation_period",
    "exists_on_kalshi",
    "native_tier",
    "native_endpoint",
    "event_ticker",
    "status",
    "result",
    "title",
    "open_time",
    "close_time",
    "settlement_time",
    "fractional_trading_enabled",
    "volume_fp",
]


def missing_tickers() -> list[str]:
    with PREDEXON_MANIFEST.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    values = payload.get("missing_tickers")
    if not isinstance(values, list):
        raise RuntimeError(
            f"Missing ticker list not found in {PREDEXON_MANIFEST}"
        )
    return [str(value) for value in values]


def ticker_windows() -> dict[str, tuple[datetime, datetime]]:
    return {
        ticker: (
            WINDOW_START + timedelta(minutes=15 * index),
            WINDOW_START + timedelta(minutes=15 * (index + 1)),
        )
        for index, ticker in enumerate(expected_market_tickers())
    }


def check_ticker(
    client: KalshiPublicClient,
    ticker: str,
    start: datetime,
    end: datetime,
    overwrite: bool,
) -> tuple[dict[str, object], bool]:
    part_path = PART_ROOT / f"{ticker}.json"
    if part_path.exists() and not overwrite:
        with part_path.open(encoding="utf-8") as handle:
            return json.load(handle), True

    encoded = quote(ticker, safe="")
    probes = [
        ("live", f"markets/{encoded}"),
        ("historical", f"historical/markets/{encoded}"),
    ]
    market: dict | None = None
    native_tier = "not_found"
    native_endpoint = ""

    for tier, endpoint in probes:
        payload = client.get_json(endpoint, allow_not_found=True)
        if payload is not None:
            candidate = payload.get("market")
            if not isinstance(candidate, dict):
                raise RuntimeError(f"Kalshi {endpoint} response is missing market")
            market = candidate
            native_tier = tier
            native_endpoint = f"/{endpoint}"
            write_json_gzip_atomic(
                payload,
                RAW_ROOT / f"{ticker}_{tier}.json.gz",
            )
            break

    row: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "interval_start_utc": iso_utc(start),
        "interval_end_utc": iso_utc(end),
        "observation_period": treatment_period(start.timestamp()),
        "exists_on_kalshi": market is not None,
        "native_tier": native_tier,
        "native_endpoint": native_endpoint,
        "event_ticker": (market or {}).get("event_ticker", ""),
        "status": (market or {}).get("status", ""),
        "result": (market or {}).get("result", ""),
        "title": (market or {}).get("title", ""),
        "open_time": (market or {}).get("open_time", ""),
        "close_time": (market or {}).get("close_time", ""),
        "settlement_time": (market or {}).get("settlement_time", ""),
        "fractional_trading_enabled": (market or {}).get(
            "fractional_trading_enabled", ""
        ),
        "volume_fp": (market or {}).get("volume_fp", ""),
    }
    write_json_atomic(row, part_path)
    return row, False


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--overwrite-parts", action="store_true")
    args = parser.parse_args()

    tickers = missing_tickers()
    if args.max_markets > 0:
        tickers = tickers[: args.max_markets]
    windows = ticker_windows()
    client = KalshiPublicClient(
        request_interval=args.request_interval,
        run_log=run_log,
    )
    run_log.info(
        f"Missing-market audit configured tickers={len(tickers)} "
        f"partial_run={args.max_markets > 0} "
        f"overwrite_parts={args.overwrite_parts} source_manifest={PREDEXON_MANIFEST}"
    )

    rows: list[dict[str, object]] = []
    reused_parts = 0
    for index, ticker in enumerate(tickers, start=1):
        if ticker not in windows:
            raise RuntimeError(f"Missing ticker is outside expected grid: {ticker}")
        row, reused = check_ticker(
            client,
            ticker,
            *windows[ticker],
            args.overwrite_parts,
        )
        rows.append(row)
        reused_parts += int(reused)
        run_log.progress(
            index,
            len(tickers),
            f"ticker={ticker} exists={row['exists_on_kalshi']} "
            f"tier={row['native_tier']} source={'reused' if reused else 'fetched'}",
        )

    write_csv_atomic(rows, AUDIT_PATH, FIELDS)
    by_period: dict[str, dict[str, int]] = {}
    for period in ("pre", "post"):
        period_rows = [row for row in rows if row["observation_period"] == period]
        by_period[period] = {
            "predexon_missing": len(period_rows),
            "exists_on_kalshi": sum(bool(row["exists_on_kalshi"]) for row in period_rows),
            "not_found_on_kalshi": sum(
                not bool(row["exists_on_kalshi"]) for row in period_rows
            ),
        }
    tiers = Counter(str(row["native_tier"]) for row in rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "official_kalshi_public_api",
        "base_url": BASE_URL,
        "authentication_required": False,
        "predexon_missing_tickers_checked": len(rows),
        "exists_on_kalshi": sum(bool(row["exists_on_kalshi"]) for row in rows),
        "not_found_on_kalshi": sum(not bool(row["exists_on_kalshi"]) for row in rows),
        "by_native_tier": dict(tiers),
        "by_period": by_period,
        "partial_run": args.max_markets > 0,
        "reused_parts": reused_parts,
        "requests": client.request_count,
        "retries": client.retry_count,
        "audit_file": str(AUDIT_PATH),
        "log_file": str(run_log.path),
    }
    write_json_atomic(summary, SUMMARY_PATH)
    run_log.info(
        f"Missing-market audit completed checked={len(rows)} "
        f"exists_on_kalshi={summary['exists_on_kalshi']} "
        f"not_found_on_kalshi={summary['not_found_on_kalshi']} "
        f"by_period={by_period} requests={client.request_count} "
        f"retries={client.retry_count} audit={AUDIT_PATH} summary={SUMMARY_PATH}"
    )


if __name__ == "__main__":
    run_logged("audit_missing_kalshi_markets", main)

