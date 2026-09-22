#!/usr/bin/env python3
"""Reusable political-market discovery, collection, and coverage pipeline.

The pipeline validates exact Kalshi/Polymarket contract mappings using official
metadata APIs, collects exact Kalshi trades from the public Kalshi API, and
collects Polymarket trades plus both venues' historical order books from
Predexon. Political markets are controls: neither venue is marked as receiving
the crypto-only Polymarket taker-delay treatment.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # Loading an existing shell environment is also supported.
    load_dotenv = None


POLITICAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = POLITICAL_ROOT.parent
if load_dotenv is not None:
    load_dotenv(REPO_ROOT / ".env")

SCHEMA_VERSION = "2026-09-15.1"
PREDEXON_BASE_URL = "https://api.predexon.com/v2"
KALSHI_METADATA_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DATA_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"

EVENT_ALIASES = {
    "brazil": "BRAZIL_PRESIDENTIAL_ELECTION",
    "iceland": "ICELAND_EU_REFERENDUM",
    "massachusetts": "MASSACHUSETTS_DEMOCRATIC_PRIMARIES",
}

MARKET_MAP_FIELDS = [
    "pair_id",
    "event_key",
    "family",
    "group_id",
    "canonical_outcome",
    "analysis_tier",
    "comparison_start_utc",
    "treatment_time_utc",
    "comparison_end_utc",
    "control_market",
    "rule_match_status",
    "rule_notes",
    "full_window_covered",
    "kalshi_event_ticker",
    "kalshi_ticker",
    "kalshi_market_id",
    "kalshi_title",
    "kalshi_rules_primary",
    "kalshi_open_time_utc",
    "kalshi_close_time_utc",
    "kalshi_volume",
    "polymarket_event_id",
    "polymarket_event_slug",
    "polymarket_market_id",
    "polymarket_condition_id",
    "polymarket_market_slug",
    "polymarket_title",
    "polymarket_yes_token_id",
    "polymarket_no_token_id",
    "polymarket_start_time_utc",
    "polymarket_end_time_utc",
    "polymarket_volume_usd",
    "polymarket_liquidity_usd",
]

TRADE_FIELDS = [
    "schema_version",
    "pair_id",
    "event_key",
    "family",
    "group_id",
    "canonical_outcome",
    "analysis_tier",
    "platform",
    "record_unit",
    "aggregation_method",
    "transaction_fill_count",
    "instrument_id",
    "condition_id",
    "token_id",
    "outcome",
    "trade_id",
    "timestamp",
    "timestamp_ms",
    "observation_period",
    "control_market",
    "treatment_received",
    "taker_delay_ms",
    "price",
    "canonical_yes_price",
    "size",
    "size_source",
    "size_is_exact",
    "notional_usd",
    "raw_token_notional_usd",
    "reported_side",
    "taker_side",
    "tx_hash",
    "order_hash",
    "user",
    "taker",
    "fee_usd",
    "metadata",
]

BBO_FIELDS = [
    "schema_version",
    "pair_id",
    "event_key",
    "family",
    "group_id",
    "canonical_outcome",
    "analysis_tier",
    "platform",
    "instrument_id",
    "condition_id",
    "outcome",
    "timestamp_ms",
    "indexed_at_ms",
    "observation_period",
    "control_market",
    "treatment_received",
    "taker_delay_ms",
    "best_bid",
    "best_ask",
    "canonical_yes_best_bid",
    "canonical_yes_best_ask",
    "mid_price",
    "spread",
    "best_bid_depth_contracts",
    "best_ask_depth_contracts",
    "full_bid_depth_contracts",
    "full_ask_depth_contracts",
    "full_bid_depth_notional",
    "full_ask_depth_notional",
    "book_hash",
    "sequence",
    "capture_source",
    "tick_size",
    "bids_json",
    "asks_json",
]


def parse_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("missing timestamp")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: object) -> str:
    return parse_utc(value).isoformat().replace("+00:00", "Z")


def epoch_seconds(value: object) -> int:
    return int(parse_utc(value).timestamp())


def epoch_milliseconds(value: object) -> int:
    return int(parse_utc(value).timestamp() * 1_000)


def safe_filename(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return cleaned or "unknown"


def as_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def write_json_atomic(payload: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def write_json_gzip_atomic(payload: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(output)


def write_csv_atomic(
    rows: Iterable[dict[str, object]],
    output: Path,
    fieldnames: list[str],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def write_csv_gzip_atomic(
    rows: Iterable[dict[str, object]],
    output: Path,
    fieldnames: list[str],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def read_csv(input_path: Path) -> list[dict[str, str]]:
    if not input_path.exists():
        raise FileNotFoundError(f"Required input does not exist: {input_path}")
    with input_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_csv_gzip(input_path: Path) -> Iterator[dict[str, str]]:
    if not input_path.exists():
        return iter(())
    with gzip.open(input_path, "rt", newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3_600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


class RunLog:
    """Timestamped event-level log mirrored to standard output."""

    def __init__(self, event_root: Path, stage: str):
        self.started_at = time.monotonic()
        log_root = event_root / "data/logs"
        log_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = log_root / f"{stamp}_{safe_filename(stage)}.log"
        self.logger = logging.getLogger(f"politics.{event_root.name}.{stage}.{stamp}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        formatter = logging.Formatter(
            "%(asctime)sZ | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        file_handler = logging.FileHandler(self.path, encoding="utf-8")
        stream_handler = logging.StreamHandler(sys.stdout)
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(stream_handler)
        self.info(f"Run started stage={stage} event={event_root.name}")

    def info(self, message: str) -> None:
        self.logger.info(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def exception(self, message: str) -> None:
        self.logger.exception(message)

    def progress(self, completed: int, total: int, details: str = "") -> None:
        elapsed = time.monotonic() - self.started_at
        percent = completed / total * 100 if total else 100.0
        eta = elapsed / completed * (total - completed) if completed else 0.0
        suffix = f" {details}" if details else ""
        self.info(
            f"Progress {completed}/{total} ({percent:.1f}%) "
            f"elapsed={format_duration(elapsed)} eta={format_duration(eta)}{suffix}"
        )

    def close(self) -> None:
        for handler in list(self.logger.handlers):
            handler.flush()
            handler.close()
            self.logger.removeHandler(handler)


def run_logged(
    event_root: Path,
    stage: str,
    operation: Callable[[RunLog], None],
) -> None:
    run_log = RunLog(event_root, stage)
    try:
        operation(run_log)
    except KeyboardInterrupt:
        run_log.warning(
            f"Run interrupted elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
        raise
    except Exception:
        run_log.exception(
            f"Run failed elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
        raise
    else:
        run_log.info(
            f"Run completed elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
    finally:
        run_log.close()


class JsonClient:
    """Rate-limited JSON client with bounded exponential-backoff retries."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        request_interval: float = 0.1,
        max_attempts: int = 7,
        run_log: RunLog | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_interval = max(0.0, request_interval)
        self.max_attempts = max(1, max_attempts)
        self.max_retry_wait = 60.0
        self.run_log = run_log
        self.request_count = 0
        self.retry_count = 0
        self.session = requests.Session()
        self.session.headers.update(
            headers or {"User-Agent": "Polymarket-vs-Kalshi-research/1.0"}
        )
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        remaining = self.request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def get_json(
        self,
        endpoint: str,
        params: dict[str, object] | list[tuple[str, object]] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                self.request_count += 1
                response = self.session.get(url, params=params or {}, timeout=(10, 90))
                self._last_request_at = time.monotonic()
                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError(f"{url} did not return a JSON object")
                    return payload
                message = response.text[:500].replace("\n", " ")
                last_error = RuntimeError(
                    f"HTTP {response.status_code} from {url}: {message}"
                )
                if response.status_code != 429 and response.status_code < 500:
                    raise last_error
                delay = 0.75 * (2 ** min(attempt - 1, 7))
            except (requests.RequestException, ValueError, RuntimeError) as error:
                last_error = error
                if isinstance(error, RuntimeError) and "HTTP 4" in str(error) and "HTTP 429" not in str(error):
                    raise
                delay = 0.75 * (2 ** min(attempt - 1, 7))
            if attempt < self.max_attempts:
                self.retry_count += 1
                wait_seconds = min(self.max_retry_wait, delay) + random.uniform(0, 0.25)
                if self.run_log is not None:
                    self.run_log.warning(
                        f"Retry endpoint={endpoint} attempt={attempt}/{self.max_attempts} "
                        f"wait_seconds={wait_seconds:.2f} reason={last_error}"
                    )
                time.sleep(wait_seconds)
        raise RuntimeError(
            f"Request failed after {self.max_attempts} attempts: {last_error}"
        )

    def predexon_pages(
        self,
        endpoint: str,
        params: dict[str, object],
        item_key: str,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        cursor: str | None = None
        seen: set[str] = set()
        page_number = 1
        while True:
            page_params = dict(params)
            if cursor:
                page_params["pagination_key"] = cursor
            payload = self.get_json(endpoint, page_params)
            items = payload.get(item_key)
            if not isinstance(items, list):
                raise RuntimeError(f"Missing list '{item_key}' from {endpoint}")
            yield page_number, payload
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                return
            next_cursor = pagination.get("pagination_key")
            if not next_cursor or str(next_cursor) in seen:
                raise RuntimeError(f"Invalid or repeated pagination cursor from {endpoint}")
            seen.add(str(next_cursor))
            cursor = str(next_cursor)
            page_number += 1

    def kalshi_pages(
        self,
        endpoint: str,
        params: dict[str, object],
        item_key: str,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        cursor: str | None = None
        seen: set[str] = set()
        page_number = 1
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            payload = self.get_json(endpoint, page_params)
            items = payload.get(item_key)
            if not isinstance(items, list):
                raise RuntimeError(f"Missing list '{item_key}' from {endpoint}")
            yield page_number, payload
            next_cursor = payload.get("cursor")
            if not next_cursor:
                return
            if not items or str(next_cursor) in seen:
                raise RuntimeError(f"Invalid or repeated Kalshi cursor from {endpoint}")
            seen.add(str(next_cursor))
            cursor = str(next_cursor)
            page_number += 1


def official_client(base_url: str, run_log: RunLog, request_interval: float | None) -> JsonClient:
    return JsonClient(
        base_url,
        request_interval=0.1 if request_interval is None else request_interval,
        run_log=run_log,
    )


def predexon_client(run_log: RunLog, request_interval: float | None) -> JsonClient:
    api_key = os.getenv("PREDX_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"Missing PREDX_API_KEY in the environment or {REPO_ROOT / '.env'}"
        )
    configured_interval = (
        float(os.getenv("PREDEXON_REQUEST_INTERVAL_SECONDS", "0.012"))
        if request_interval is None
        else request_interval
    )
    return JsonClient(
        PREDEXON_BASE_URL,
        headers={"x-api-key": api_key},
        request_interval=configured_interval,
        max_attempts=int(os.getenv("PREDEXON_MAX_ATTEMPTS", "7")),
        run_log=run_log,
    )


def config_path(event_root: Path) -> Path:
    return event_root / "event_config.json"


def load_config(event_root: Path) -> dict[str, Any]:
    path = config_path(event_root)
    if not path.exists():
        raise FileNotFoundError(f"Missing event configuration: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ["event_key", "family", "study_window", "market_groups"]
    missing = [field for field in required if field not in config]
    if missing:
        raise ValueError(f"{path} is missing required fields: {missing}")

    window = config["study_window"]
    start = parse_utc(window["start_utc"])
    treatment = parse_utc(window["treatment_utc"])
    end = parse_utc(window["end_utc"])
    if not start < treatment < end:
        raise ValueError("Study window must satisfy start < treatment < end")
    if treatment - start != timedelta(days=7) or end - treatment != timedelta(days=7):
        raise ValueError("Political control window must be exactly seven days per period")

    pair_ids: set[str] = set()
    tickers: set[str] = set()
    slugs: set[str] = set()
    for group in config["market_groups"]:
        for market in group.get("markets", []):
            for field in [
                "pair_id",
                "canonical_outcome",
                "analysis_tier",
                "kalshi_ticker",
                "polymarket_market_slug",
            ]:
                if not str(market.get(field, "")).strip():
                    raise ValueError(f"Market in {group.get('group_id')} lacks {field}")
            if market["analysis_tier"] not in {"primary", "robustness"}:
                raise ValueError(f"Invalid analysis_tier for {market['pair_id']}")
            if market["pair_id"] in pair_ids:
                raise ValueError(f"Duplicate pair_id: {market['pair_id']}")
            if market["kalshi_ticker"] in tickers:
                raise ValueError(f"Duplicate Kalshi ticker: {market['kalshi_ticker']}")
            if market["polymarket_market_slug"] in slugs:
                raise ValueError(
                    f"Duplicate Polymarket slug: {market['polymarket_market_slug']}"
                )
            pair_ids.add(market["pair_id"])
            tickers.add(market["kalshi_ticker"])
            slugs.add(market["polymarket_market_slug"])
    if not pair_ids:
        raise ValueError(f"{path} defines no markets")
    return config


def classify_period(timestamp_seconds: int | float, treatment_time: datetime) -> str:
    return "pre" if float(timestamp_seconds) < treatment_time.timestamp() else "post"


def select_pairs(
    rows: list[dict[str, str]],
    tier: str,
    max_markets: int,
) -> list[dict[str, str]]:
    selected = rows if tier == "all" else [
        row for row in rows if row["analysis_tier"] == tier
    ]
    return selected[:max_markets] if max_markets > 0 else selected


def as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return decoded
    raise ValueError(f"Expected an array or JSON-encoded array, received {value!r}")


def binary_tokens(market: dict[str, Any]) -> tuple[str, str]:
    outcomes = [str(item).strip().lower() for item in as_list(market.get("outcomes"))]
    token_ids = [str(item) for item in as_list(market.get("clobTokenIds"))]
    if len(outcomes) != len(token_ids):
        raise ValueError("Polymarket outcomes and token IDs have different lengths")
    token_by_outcome = dict(zip(outcomes, token_ids))
    if "yes" not in token_by_outcome or "no" not in token_by_outcome:
        raise ValueError(f"Polymarket market is not binary Yes/No: {outcomes}")
    return token_by_outcome["yes"], token_by_outcome["no"]


def optional_iso(value: object) -> str:
    return iso_utc(value) if value not in (None, "") else ""


def interval_covers(
    open_time: object,
    close_time: object,
    start: datetime,
    end: datetime,
) -> bool:
    try:
        return parse_utc(open_time) <= start and parse_utc(close_time) >= end
    except (TypeError, ValueError):
        return False


def build_market_map(
    event_root: Path,
    *,
    request_interval: float | None,
    run_log: RunLog,
) -> None:
    config = load_config(event_root)
    window = config["study_window"]
    start = parse_utc(window["start_utc"])
    treatment = parse_utc(window["treatment_utc"])
    end = parse_utc(window["end_utc"])

    kalshi = official_client(KALSHI_METADATA_BASE_URL, run_log, request_interval)
    polymarket = official_client(POLYMARKET_BASE_URL, run_log, request_interval)
    rows: list[dict[str, object]] = []

    for group_index, group in enumerate(config["market_groups"], start=1):
        group_id = group["group_id"]
        kalshi_ticker = group["kalshi_event_ticker"]
        poly_slug = group["polymarket_event_slug"]
        kalshi_payload = kalshi.get_json(
            f"events/{kalshi_ticker}", {"with_nested_markets": "true"}
        )
        poly_payload = polymarket.get_json(f"events/slug/{poly_slug}")

        write_json_atomic(
            kalshi_payload,
            event_root / f"data/raw/kalshi/metadata/{safe_filename(group_id)}.json",
        )
        write_json_atomic(
            poly_payload,
            event_root / f"data/raw/polymarket/metadata/{safe_filename(group_id)}.json",
        )

        kalshi_event = kalshi_payload.get("event") or kalshi_payload
        kalshi_markets = (
            kalshi_event.get("markets") or kalshi_payload.get("markets") or []
        )
        if not isinstance(kalshi_markets, list):
            raise RuntimeError(f"Kalshi event {kalshi_ticker} has no market list")
        kalshi_by_ticker = {
            str(market.get("ticker")): market for market in kalshi_markets
        }

        if isinstance(poly_payload, list):
            if len(poly_payload) != 1:
                raise RuntimeError(
                    f"Expected one Polymarket event for {poly_slug}; found {len(poly_payload)}"
                )
            poly_event = poly_payload[0]
        else:
            poly_event = poly_payload
        poly_markets = poly_event.get("markets") or []
        if not isinstance(poly_markets, list):
            raise RuntimeError(f"Polymarket event {poly_slug} has no market list")
        poly_by_slug = {
            str(market.get("slug")): market for market in poly_markets
        }

        for spec in group["markets"]:
            try:
                kalshi_market = kalshi_by_ticker[spec["kalshi_ticker"]]
            except KeyError as error:
                raise RuntimeError(
                    f"Configured Kalshi market not found: {spec['kalshi_ticker']}"
                ) from error
            try:
                poly_market = poly_by_slug[spec["polymarket_market_slug"]]
            except KeyError as error:
                raise RuntimeError(
                    "Configured Polymarket market not found: "
                    f"{spec['polymarket_market_slug']}"
                ) from error

            yes_token, no_token = binary_tokens(poly_market)
            kalshi_covered = interval_covers(
                kalshi_market.get("open_time"),
                kalshi_market.get("close_time")
                or kalshi_market.get("latest_expiration_time"),
                start,
                end,
            )
            poly_covered = interval_covers(
                poly_market.get("startDate")
                or poly_market.get("startTime")
                or poly_event.get("startDate"),
                poly_market.get("endDate") or poly_event.get("endDate"),
                start,
                end,
            )
            full_window = kalshi_covered and poly_covered
            if config.get("require_full_window", True) and not full_window:
                raise RuntimeError(
                    f"{spec['pair_id']} does not cover the full configured window "
                    f"(kalshi={kalshi_covered}, polymarket={poly_covered})"
                )

            rows.append(
                {
                    "pair_id": spec["pair_id"],
                    "event_key": config["event_key"],
                    "family": config["family"],
                    "group_id": group_id,
                    "canonical_outcome": spec["canonical_outcome"],
                    "analysis_tier": spec["analysis_tier"],
                    "comparison_start_utc": iso_utc(start),
                    "treatment_time_utc": iso_utc(treatment),
                    "comparison_end_utc": iso_utc(end),
                    "control_market": True,
                    "rule_match_status": spec.get(
                        "rule_match_status",
                        "substantively_matched_manual_review_required",
                    ),
                    "rule_notes": spec.get("rule_notes", ""),
                    "full_window_covered": full_window,
                    "kalshi_event_ticker": kalshi_ticker,
                    "kalshi_ticker": kalshi_market.get("ticker", ""),
                    "kalshi_market_id": kalshi_market.get("market_id", ""),
                    "kalshi_title": kalshi_market.get("title", ""),
                    "kalshi_rules_primary": kalshi_market.get("rules_primary", ""),
                    "kalshi_open_time_utc": optional_iso(
                        kalshi_market.get("open_time")
                    ),
                    "kalshi_close_time_utc": optional_iso(
                        kalshi_market.get("close_time")
                        or kalshi_market.get("latest_expiration_time")
                    ),
                    "kalshi_volume": kalshi_market.get(
                        "volume_fp", kalshi_market.get("volume", "")
                    ),
                    "polymarket_event_id": poly_event.get("id", ""),
                    "polymarket_event_slug": poly_event.get("slug", ""),
                    "polymarket_market_id": poly_market.get("id", ""),
                    "polymarket_condition_id": poly_market.get("conditionId", ""),
                    "polymarket_market_slug": poly_market.get("slug", ""),
                    "polymarket_title": poly_market.get("question", ""),
                    "polymarket_yes_token_id": yes_token,
                    "polymarket_no_token_id": no_token,
                    "polymarket_start_time_utc": optional_iso(
                        poly_market.get("startDate")
                        or poly_market.get("startTime")
                        or poly_event.get("startDate")
                    ),
                    "polymarket_end_time_utc": optional_iso(
                        poly_market.get("endDate") or poly_event.get("endDate")
                    ),
                    "polymarket_volume_usd": poly_market.get("volume", ""),
                    "polymarket_liquidity_usd": poly_market.get("liquidity", ""),
                }
            )
        run_log.progress(
            group_index,
            len(config["market_groups"]),
            f"group={group_id} mapped={len(group['markets'])}",
        )

    if len({row["pair_id"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate pair IDs in generated market map")
    if len({row["kalshi_ticker"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate Kalshi tickers in generated market map")
    if len({row["polymarket_condition_id"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate Polymarket conditions in generated market map")

    output = event_root / "data/matched_markets/market_map.csv"
    write_csv_atomic(rows, output, MARKET_MAP_FIELDS)
    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_by_venue": {
                "kalshi": "official_public_api",
                "polymarket": "official_gamma_api",
            },
            "event_key": config["event_key"],
            "groups": len(config["market_groups"]),
            "matched_pairs": len(rows),
            "primary_pairs": sum(row["analysis_tier"] == "primary" for row in rows),
            "robustness_pairs": sum(
                row["analysis_tier"] == "robustness" for row in rows
            ),
            "full_window_pairs": sum(bool(row["full_window_covered"]) for row in rows),
            "market_map": str(output),
            "log_file": str(run_log.path),
        },
        event_root / "data/matched_markets/market_map_manifest.json",
    )
    run_log.info(f"Validated and wrote {len(rows)} matched pairs to {output}")


def native_trade_timestamp(trade: dict[str, Any]) -> tuple[int, int]:
    raw = trade.get("created_time")
    if raw in (None, ""):
        raise RuntimeError("Kalshi trade is missing created_time")
    if isinstance(raw, (int, float)) or str(raw).replace(".", "", 1).isdigit():
        numeric = float(raw)
        if numeric >= 1_000_000_000_000:
            return int(numeric // 1_000), int(numeric)
        return int(numeric), int(numeric * 1_000)
    parsed = parse_utc(raw)
    return int(parsed.timestamp()), int(parsed.timestamp() * 1_000)


def normalize_kalshi_trade(
    pair: dict[str, str],
    trade: dict[str, Any],
    treatment_time: datetime,
) -> dict[str, object]:
    trade_id = str(trade.get("trade_id") or "").strip()
    size = as_decimal(trade.get("count_fp"))
    yes_price = as_decimal(trade.get("yes_price_dollars"))
    if not trade_id or size is None or yes_price is None:
        raise RuntimeError(
            "Official Kalshi trade lacks trade_id, count_fp, or yes_price_dollars"
        )
    timestamp, timestamp_ms = native_trade_timestamp(trade)
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "event_key": pair["event_key"],
        "family": pair["family"],
        "group_id": pair["group_id"],
        "canonical_outcome": pair["canonical_outcome"],
        "analysis_tier": pair["analysis_tier"],
        "platform": "kalshi",
        "record_unit": "exchange_trade",
        "aggregation_method": "native_trade_id",
        "transaction_fill_count": 1,
        "instrument_id": pair["kalshi_ticker"],
        "condition_id": "",
        "token_id": "",
        "outcome": "Yes",
        "trade_id": trade_id,
        "timestamp": timestamp,
        "timestamp_ms": timestamp_ms,
        "observation_period": classify_period(timestamp, treatment_time),
        "control_market": True,
        "treatment_received": False,
        "taker_delay_ms": "",
        "price": decimal_text(yes_price),
        "canonical_yes_price": decimal_text(yes_price),
        "size": decimal_text(size),
        "size_source": "kalshi_native_count_fp",
        "size_is_exact": True,
        "notional_usd": decimal_text(yes_price * size),
        "raw_token_notional_usd": decimal_text(yes_price * size),
        "reported_side": "",
        "taker_side": trade.get("taker_side", ""),
        "tx_hash": "",
        "order_hash": "",
        "user": "",
        "taker": "",
        "fee_usd": "",
        "metadata": "",
    }


def normalize_polymarket_trade(
    pair: dict[str, str],
    trade: dict[str, Any],
    treatment_time: datetime,
) -> dict[str, object]:
    timestamp = int(trade["timestamp"])
    price = as_float(trade.get("price"))
    size = as_float(trade.get("shares_normalized"))
    size_source = "shares_normalized"
    if size is None:
        raw_size = as_float(trade.get("shares"))
        size = raw_size / 1_000_000 if raw_size is not None else None
        size_source = "shares_scaled_1e6" if size is not None else "missing"
    token_id = str(trade.get("token_id") or "")
    outcome = str(trade.get("outcome_label") or "")
    is_yes_side = trade.get("is_yes_side")
    if token_id == pair["polymarket_yes_token_id"] or is_yes_side is True or outcome.lower() == "yes":
        canonical_price = price
    elif token_id == pair["polymarket_no_token_id"] or is_yes_side is False or outcome.lower() == "no":
        canonical_price = 1 - price if price is not None else None
    else:
        raise RuntimeError(
            f"Could not orient Polymarket trade for pair {pair['pair_id']}"
        )
    canonical_notional = (
        canonical_price * size
        if canonical_price is not None and size is not None
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "event_key": pair["event_key"],
        "family": pair["family"],
        "group_id": pair["group_id"],
        "canonical_outcome": pair["canonical_outcome"],
        "analysis_tier": pair["analysis_tier"],
        "platform": "polymarket",
        "record_unit": "predexon_fill",
        "aggregation_method": "pending_transaction_collapse",
        "transaction_fill_count": 1,
        "instrument_id": token_id,
        "condition_id": pair["polymarket_condition_id"],
        "token_id": token_id,
        "outcome": outcome,
        "trade_id": "",
        "timestamp": timestamp,
        "timestamp_ms": trade.get("timestamp_ms") or timestamp * 1_000,
        "observation_period": classify_period(timestamp, treatment_time),
        "control_market": True,
        "treatment_received": False,
        "taker_delay_ms": "",
        "price": price,
        "canonical_yes_price": canonical_price,
        "size": size,
        "size_source": size_source,
        "size_is_exact": trade.get("shares") not in (None, ""),
        "notional_usd": canonical_notional,
        "raw_token_notional_usd": as_float(trade.get("amount_usd")),
        "reported_side": trade.get("side", ""),
        "taker_side": "",
        "tx_hash": trade.get("tx_hash", ""),
        "order_hash": trade.get("order_hash", ""),
        "user": trade.get("user", ""),
        "taker": trade.get("taker", ""),
        "fee_usd": trade.get("fee_usd", ""),
        "metadata": trade.get("metadata", ""),
    }


def collapse_polymarket_transaction_fills(
    rows: list[dict[str, object]],
    pair_id: str,
) -> list[dict[str, object]]:
    """Keep one aggregate taker leg per on-chain Polymarket transaction.

    Predexon returns every matched maker leg plus one aggregate leg for the
    taker's order. Within a transaction, that aggregate row is identified by
    the fact that its ``user`` address appears as the ``taker`` address on the
    counterparty rows. Keeping all legs would double-count activity and make
    the row count depend on how many resting orders the taker crossed.
    """
    unique_fills: dict[tuple[object, ...], dict[str, object]] = {}
    for row in rows:
        marker = (
            row["tx_hash"],
            row["order_hash"],
            row["token_id"],
            row["user"],
            row["size"],
            row["price"],
        )
        unique_fills[marker] = row

    by_transaction: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in unique_fills.values():
        tx_hash = str(row.get("tx_hash") or "").strip()
        if not tx_hash:
            raise RuntimeError(
                f"Polymarket fill is missing tx_hash for pair {pair_id}"
            )
        by_transaction[tx_hash].append(row)

    transactions: list[dict[str, object]] = []
    for tx_hash, fills in by_transaction.items():
        if len(fills) == 1:
            selected = fills[0]
            method = "single_fill_transaction"
        else:
            taker_addresses = {
                str(fill.get("taker") or "").lower()
                for fill in fills
                if str(fill.get("taker") or "").strip()
            }
            candidates = [
                fill
                for fill in fills
                if str(fill.get("user") or "").lower() in taker_addresses
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    "Could not identify exactly one Polymarket aggregate taker "
                    f"leg for pair={pair_id} tx_hash={tx_hash} "
                    f"fills={len(fills)} candidates={len(candidates)}"
                )
            selected = candidates[0]
            method = "user_matches_counterparty_taker"

        transaction = dict(selected)
        transaction["trade_id"] = tx_hash
        transaction["record_unit"] = "onchain_transaction"
        transaction["aggregation_method"] = method
        transaction["transaction_fill_count"] = len(fills)
        transaction["size_source"] = (
            f"{transaction['size_source']}_aggregate_taker_leg"
        )
        transactions.append(transaction)

    return sorted(
        transactions,
        key=lambda row: (int(row["timestamp_ms"]), str(row["tx_hash"])),
    )


def write_trade_part(rows: list[dict[str, object]], output: Path) -> None:
    write_csv_gzip_atomic(rows, output, TRADE_FIELDS)


def kalshi_trade_segments(
    start: int,
    end: int,
    cutoff: int,
) -> list[tuple[str, str, int, int]]:
    segments: list[tuple[str, str, int, int]] = []
    if start < cutoff:
        segments.append(("historical", "historical/trades", start, min(end, cutoff)))
    if end > cutoff:
        segments.append(("live", "markets/trades", max(start, cutoff), end))
    return [segment for segment in segments if segment[2] < segment[3]]


def collect_kalshi_trades(
    client: JsonClient,
    event_root: Path,
    pair: dict[str, str],
    treatment_time: datetime,
    cutoff: int,
    overwrite: bool,
) -> tuple[Path, int, Counter[str], bool]:
    part_path = (
        event_root
        / f"data/processed/_parts/trades/kalshi/{safe_filename(pair['pair_id'])}.csv.gz"
    )
    if part_path.exists() and not overwrite:
        return part_path, sum(1 for _ in read_csv_gzip(part_path)), Counter(), True

    start = epoch_seconds(pair["comparison_start_utc"])
    end = epoch_seconds(pair["comparison_end_utc"])
    rows_by_id: dict[str, dict[str, object]] = {}
    pages: Counter[str] = Counter()
    for segment_name, endpoint, segment_start, segment_end in kalshi_trade_segments(
        start, end, cutoff
    ):
        params = {
            "ticker": pair["kalshi_ticker"],
            "min_ts": max(0, segment_start - 1),
            "max_ts": segment_end,
            "limit": 1000,
        }
        raw_dir = (
            event_root
            / "data/raw/kalshi_native/trades"
            / safe_filename(pair["pair_id"])
            / segment_name
        )
        for page_number, payload in client.kalshi_pages(endpoint, params, "trades"):
            pages[segment_name] += 1
            write_json_gzip_atomic(
                payload, raw_dir / f"page_{page_number:05d}.json.gz"
            )
            for trade in payload["trades"]:
                row = normalize_kalshi_trade(pair, trade, treatment_time)
                if start <= int(row["timestamp"]) < end:
                    marker = str(row["trade_id"])
                    existing = rows_by_id.get(marker)
                    if existing is not None and existing != row:
                        raise RuntimeError(f"Conflicting Kalshi trade_id {marker}")
                    rows_by_id[marker] = row
    rows = sorted(
        rows_by_id.values(),
        key=lambda row: (int(row["timestamp_ms"]), str(row["trade_id"])),
    )
    write_trade_part(rows, part_path)
    return part_path, len(rows), pages, False


def collect_polymarket_trades(
    client: JsonClient,
    event_root: Path,
    pair: dict[str, str],
    treatment_time: datetime,
    overwrite: bool,
) -> tuple[Path, int, int, bool]:
    part_path = (
        event_root
        / f"data/processed/_parts/trades/polymarket/{safe_filename(pair['pair_id'])}.csv.gz"
    )
    if part_path.exists() and not overwrite:
        return part_path, sum(1 for _ in read_csv_gzip(part_path)), 0, True

    start = epoch_seconds(pair["comparison_start_utc"])
    end = epoch_seconds(pair["comparison_end_utc"])
    rows: list[dict[str, object]] = []
    pages = 0
    raw_dir = (
        event_root
        / "data/raw/polymarket/trades"
        / safe_filename(pair["pair_id"])
    )
    params = {
        "condition_id": pair["polymarket_condition_id"],
        "start_time": start,
        "end_time": end,
        "limit": 500,
        "order": "asc",
    }
    for page_number, payload in client.predexon_pages(
        "polymarket/trades", params, "trades"
    ):
        pages = page_number
        write_json_gzip_atomic(
            payload, raw_dir / f"page_{page_number:05d}.json.gz"
        )
        for trade in payload["trades"]:
            row = normalize_polymarket_trade(pair, trade, treatment_time)
            if start <= int(row["timestamp"]) < end:
                rows.append(row)

    rows = collapse_polymarket_transaction_fills(rows, pair["pair_id"])
    write_trade_part(rows, part_path)
    return part_path, len(rows), pages, False


def combine_gzip_parts(
    part_paths: list[Path],
    output: Path,
    fieldnames: list[str],
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    count = 0
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for part_path in sorted(part_paths):
            for row in read_csv_gzip(part_path):
                writer.writerow(row)
                count += 1
    temporary.replace(output)
    return count


def fetch_trades(
    event_root: Path,
    *,
    venue: str,
    tier: str,
    max_markets: int,
    request_interval: float | None,
    overwrite_parts: bool,
    run_log: RunLog,
) -> None:
    config = load_config(event_root)
    treatment = parse_utc(config["study_window"]["treatment_utc"])
    pairs = select_pairs(
        read_csv(event_root / "data/matched_markets/market_map.csv"),
        tier,
        max_markets,
    )
    venues = ["kalshi", "polymarket"] if venue == "both" else [venue]
    clients: dict[str, JsonClient] = {}
    cutoff = 0
    if "kalshi" in venues:
        clients["kalshi"] = official_client(
            KALSHI_DATA_BASE_URL, run_log, request_interval
        )
        cutoff_payload = clients["kalshi"].get_json("historical/cutoff")
        cutoff = epoch_seconds(cutoff_payload["trades_created_ts"])
    if "polymarket" in venues:
        clients["polymarket"] = predexon_client(run_log, request_interval)

    parts: dict[str, list[Path]] = {"kalshi": [], "polymarket": []}
    row_counts = Counter()
    page_counts = Counter()
    reused = Counter()
    empty = Counter()
    for pair_index, pair in enumerate(pairs, start=1):
        details: list[str] = []
        if "kalshi" in venues:
            part, count, pages, was_reused = collect_kalshi_trades(
                clients["kalshi"],
                event_root,
                pair,
                treatment,
                cutoff,
                overwrite_parts,
            )
            parts["kalshi"].append(part)
            row_counts["kalshi"] += count
            page_counts.update({f"kalshi_{key}": value for key, value in pages.items()})
            reused["kalshi"] += int(was_reused)
            empty["kalshi"] += int(count == 0)
            details.append(f"kalshi_rows={count}")
        if "polymarket" in venues:
            part, count, pages, was_reused = collect_polymarket_trades(
                clients["polymarket"],
                event_root,
                pair,
                treatment,
                overwrite_parts,
            )
            parts["polymarket"].append(part)
            row_counts["polymarket"] += count
            page_counts["polymarket"] += pages
            reused["polymarket"] += int(was_reused)
            empty["polymarket"] += int(count == 0)
            details.append(f"polymarket_rows={count}")
        run_log.progress(
            pair_index,
            len(pairs),
            f"pair={pair['pair_id']} {' '.join(details)}",
        )

    output_counts: dict[str, int] = {}
    for platform in venues:
        output = event_root / f"data/processed/{platform}/trades.csv.gz"
        output_counts[platform] = combine_gzip_parts(
            parts[platform], output, TRADE_FIELDS
        )
    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_key": config["event_key"],
            "market_map": str(
                event_root / "data/matched_markets/market_map.csv"
            ),
            "tier": tier,
            "pairs_selected": len(pairs),
            "venues": venues,
            "sources": {
                "kalshi": "official_public_api_exact_count_fp",
                "polymarket": "predexon_one_aggregate_taker_leg_per_tx_hash",
            },
            "record_units": {
                "kalshi": "exchange_trade",
                "polymarket": "onchain_transaction",
            },
            "rows": output_counts,
            "pages": dict(page_counts),
            "reused_parts": dict(reused),
            "empty_markets": dict(empty),
            "log_file": str(run_log.path),
        },
        event_root / "data/processed/trades_collection_manifest.json",
    )


def normalized_levels(
    levels: object,
    *,
    prices_are_cents: bool,
) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for level in levels if isinstance(levels, list) else []:
        if not isinstance(level, dict):
            continue
        price = as_float(level.get("price"))
        size = as_float(level.get("size"))
        if price is None or size is None or size < 0:
            continue
        if prices_are_cents:
            price /= 100
        if not 0 < price < 1:
            continue
        result.append({"price": price, "size": size})
    return result


def level_metrics(
    levels: list[dict[str, float]],
    best_price: float | None,
) -> tuple[float, float, float]:
    full_contracts = sum(level["size"] for level in levels)
    full_notional = sum(level["price"] * level["size"] for level in levels)
    best_depth = (
        sum(level["size"] for level in levels if level["price"] == best_price)
        if best_price is not None
        else 0.0
    )
    return best_depth, full_contracts, full_notional


def normalize_snapshot(
    pair: dict[str, str],
    platform: str,
    instrument_id: str,
    snapshot: dict[str, Any],
    treatment_time: datetime,
) -> dict[str, object]:
    prices_are_cents = platform == "kalshi"
    bids = normalized_levels(
        snapshot.get("yes_bids") if prices_are_cents else snapshot.get("bids"),
        prices_are_cents=prices_are_cents,
    )
    asks = normalized_levels(
        snapshot.get("yes_asks") if prices_are_cents else snapshot.get("asks"),
        prices_are_cents=prices_are_cents,
    )
    best_bid = max((level["price"] for level in bids), default=None)
    best_ask = min((level["price"] for level in asks), default=None)
    bid_best_depth, bid_full_depth, bid_notional = level_metrics(bids, best_bid)
    ask_best_depth, ask_full_depth, ask_notional = level_metrics(asks, best_ask)
    timestamp_ms = int(snapshot["timestamp"])
    timestamp_seconds = timestamp_ms / 1_000
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "event_key": pair["event_key"],
        "family": pair["family"],
        "group_id": pair["group_id"],
        "canonical_outcome": pair["canonical_outcome"],
        "analysis_tier": pair["analysis_tier"],
        "platform": platform,
        "instrument_id": instrument_id,
        "condition_id": (
            pair["polymarket_condition_id"] if platform == "polymarket" else ""
        ),
        "outcome": "Yes",
        "timestamp_ms": timestamp_ms,
        "indexed_at_ms": snapshot.get("indexedAt", ""),
        "observation_period": classify_period(timestamp_seconds, treatment_time),
        "control_market": True,
        "treatment_received": False,
        "taker_delay_ms": "",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "canonical_yes_best_bid": best_bid,
        "canonical_yes_best_ask": best_ask,
        "mid_price": (
            (best_bid + best_ask) / 2
            if best_bid is not None and best_ask is not None
            else None
        ),
        "spread": (
            best_ask - best_bid
            if best_bid is not None and best_ask is not None
            else None
        ),
        "best_bid_depth_contracts": bid_best_depth,
        "best_ask_depth_contracts": ask_best_depth,
        "full_bid_depth_contracts": bid_full_depth,
        "full_ask_depth_contracts": ask_full_depth,
        "full_bid_depth_notional": bid_notional,
        "full_ask_depth_notional": ask_notional,
        "book_hash": snapshot.get("hash", ""),
        "sequence": snapshot.get("sequence", ""),
        "capture_source": snapshot.get("source", ""),
        "tick_size": snapshot.get("tickSize", ""),
        "bids_json": json.dumps(bids, separators=(",", ":")),
        "asks_json": json.dumps(asks, separators=(",", ":")),
    }


def collect_bbo_instrument(
    client: JsonClient,
    event_root: Path,
    pair: dict[str, str],
    platform: str,
    treatment_time: datetime,
    overwrite: bool,
) -> tuple[Path, int, int, bool]:
    instrument_id = (
        pair["kalshi_ticker"]
        if platform == "kalshi"
        else pair["polymarket_yes_token_id"]
    )
    part_path = (
        event_root
        / f"data/processed/_parts/bbo/{platform}/{safe_filename(pair['pair_id'])}.csv.gz"
    )
    if part_path.exists() and not overwrite:
        return part_path, sum(1 for _ in read_csv_gzip(part_path)), 0, True

    start_ms = epoch_milliseconds(pair["comparison_start_utc"])
    end_ms = epoch_milliseconds(pair["comparison_end_utc"])
    if platform == "kalshi":
        endpoint = "kalshi/orderbooks-subcent"
        params = {
            "ticker": instrument_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 2000,
            "sources": "spine",
        }
        raw_name = "orderbooks_subcent"
    else:
        endpoint = "polymarket/orderbooks"
        params = {
            "token_id": instrument_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 200,
        }
        raw_name = "orderbooks"

    rows: list[dict[str, object]] = []
    pages = 0
    raw_dir = (
        event_root
        / f"data/raw/{platform}/{raw_name}/{safe_filename(pair['pair_id'])}"
    )
    for page_number, payload in client.predexon_pages(
        endpoint, params, "snapshots"
    ):
        pages = page_number
        write_json_gzip_atomic(
            payload, raw_dir / f"page_{page_number:05d}.json.gz"
        )
        for snapshot in payload["snapshots"]:
            row = normalize_snapshot(
                pair, platform, instrument_id, snapshot, treatment_time
            )
            if start_ms <= int(row["timestamp_ms"]) < end_ms:
                rows.append(row)

    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for row in rows:
        marker = (
            row["instrument_id"],
            row["timestamp_ms"],
            row["sequence"] or row["book_hash"],
        )
        unique[marker] = row
    rows = sorted(unique.values(), key=lambda row: int(row["timestamp_ms"]))
    write_csv_gzip_atomic(rows, part_path, BBO_FIELDS)
    return part_path, len(rows), pages, False


def fetch_bbo(
    event_root: Path,
    *,
    venue: str,
    tier: str,
    max_markets: int,
    request_interval: float | None,
    overwrite_parts: bool,
    run_log: RunLog,
) -> None:
    config = load_config(event_root)
    treatment = parse_utc(config["study_window"]["treatment_utc"])
    pairs = select_pairs(
        read_csv(event_root / "data/matched_markets/market_map.csv"),
        tier,
        max_markets,
    )
    venues = ["kalshi", "polymarket"] if venue == "both" else [venue]
    client = predexon_client(run_log, request_interval)
    parts: dict[str, list[Path]] = {"kalshi": [], "polymarket": []}
    row_counts = Counter()
    page_counts = Counter()
    reused = Counter()
    empty = Counter()

    for pair_index, pair in enumerate(pairs, start=1):
        details: list[str] = []
        for platform in venues:
            part, count, pages, was_reused = collect_bbo_instrument(
                client,
                event_root,
                pair,
                platform,
                treatment,
                overwrite_parts,
            )
            parts[platform].append(part)
            row_counts[platform] += count
            page_counts[platform] += pages
            reused[platform] += int(was_reused)
            empty[platform] += int(count == 0)
            details.append(f"{platform}_rows={count}")
        run_log.progress(
            pair_index,
            len(pairs),
            f"pair={pair['pair_id']} {' '.join(details)}",
        )

    output_counts: dict[str, int] = {}
    for platform in venues:
        output = event_root / f"data/processed/{platform}/bbo.csv.gz"
        output_counts[platform] = combine_gzip_parts(
            parts[platform], output, BBO_FIELDS
        )
    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_key": config["event_key"],
            "market_map": str(
                event_root / "data/matched_markets/market_map.csv"
            ),
            "tier": tier,
            "pairs_selected": len(pairs),
            "venues": venues,
            "source_by_venue": {
                "kalshi": "predexon_free_orderbooks_subcent_spine",
                "polymarket": "predexon_free_orderbooks_yes_token",
            },
            "paid_tick_data_requested": False,
            "rows": output_counts,
            "pages": dict(page_counts),
            "reused_parts": dict(reused),
            "empty_markets": dict(empty),
            "log_file": str(run_log.path),
        },
        event_root / "data/processed/bbo_collection_manifest.json",
    )


def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def audit_coverage(event_root: Path, run_log: RunLog) -> None:
    config = load_config(event_root)
    pairs = read_csv(event_root / "data/matched_markets/market_map.csv")
    expected_hours = int(
        (
            parse_utc(config["study_window"]["treatment_utc"])
            - parse_utc(config["study_window"]["start_utc"])
        ).total_seconds()
        // 3600
    )
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for pair in pairs:
        for platform in ["kalshi", "polymarket"]:
            for period in ["pre", "post"]:
                cells[(pair["pair_id"], platform, period)] = {
                    "pair_id": pair["pair_id"],
                    "event_key": pair["event_key"],
                    "group_id": pair["group_id"],
                    "canonical_outcome": pair["canonical_outcome"],
                    "analysis_tier": pair["analysis_tier"],
                    "platform": platform,
                    "observation_period": period,
                    "expected_hour_bins": expected_hours,
                    "trade_records": 0,
                    "trade_notional_usd": 0.0,
                    "exact_size_records": 0,
                    "bbo_records": 0,
                    "usable_two_sided_bbo_records": 0,
                    "_trade_hours": set(),
                    "_bbo_hours": set(),
                }

    for platform in ["kalshi", "polymarket"]:
        trade_path = event_root / f"data/processed/{platform}/trades.csv.gz"
        for row in read_csv_gzip(trade_path):
            key = (row["pair_id"], platform, row["observation_period"])
            if key not in cells:
                continue
            cell = cells[key]
            cell["trade_records"] += 1
            cell["trade_notional_usd"] += as_float(row.get("notional_usd")) or 0.0
            cell["exact_size_records"] += int(truthy(row.get("size_is_exact")))
            cell["_trade_hours"].add(int(float(row["timestamp"])) // 3600)

        bbo_path = event_root / f"data/processed/{platform}/bbo.csv.gz"
        for row in read_csv_gzip(bbo_path):
            key = (row["pair_id"], platform, row["observation_period"])
            if key not in cells:
                continue
            cell = cells[key]
            cell["bbo_records"] += 1
            bid = as_float(row.get("canonical_yes_best_bid"))
            ask = as_float(row.get("canonical_yes_best_ask"))
            usable = (
                bid is not None
                and ask is not None
                and 0 < bid <= ask < 1
            )
            cell["usable_two_sided_bbo_records"] += int(usable)
            if usable:
                cell["_bbo_hours"].add(int(float(row["timestamp_ms"])) // 3_600_000)

    output_rows: list[dict[str, object]] = []
    for key in sorted(cells):
        cell = cells[key]
        trade_hours = len(cell.pop("_trade_hours"))
        bbo_hours = len(cell.pop("_bbo_hours"))
        trade_records = int(cell["trade_records"])
        exact_records = int(cell.pop("exact_size_records"))
        cell["trade_hour_bins"] = trade_hours
        cell["bbo_hour_bins"] = bbo_hours
        cell["bbo_hour_coverage_pct"] = round(
            bbo_hours / expected_hours * 100, 3
        )
        cell["exact_size_share"] = (
            round(exact_records / trade_records, 6) if trade_records else ""
        )
        cell["passes_trade_threshold_10"] = trade_records >= 10
        cell["has_usable_bbo"] = cell["usable_two_sided_bbo_records"] > 0
        cell["trade_notional_usd"] = round(cell["trade_notional_usd"], 8)
        output_rows.append(cell)

    fields = [
        "pair_id",
        "event_key",
        "group_id",
        "canonical_outcome",
        "analysis_tier",
        "platform",
        "observation_period",
        "expected_hour_bins",
        "trade_records",
        "trade_notional_usd",
        "trade_hour_bins",
        "exact_size_share",
        "passes_trade_threshold_10",
        "bbo_records",
        "usable_two_sided_bbo_records",
        "bbo_hour_bins",
        "bbo_hour_coverage_pct",
        "has_usable_bbo",
    ]
    output = event_root / "data/audits/coverage_by_pair_platform_period.csv"
    write_csv_atomic(output_rows, output, fields)
    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_key": config["event_key"],
            "pairs": len(pairs),
            "rows": len(output_rows),
            "expected_hours_per_period": expected_hours,
            "trade_files_present": {
                platform: (
                    event_root / f"data/processed/{platform}/trades.csv.gz"
                ).exists()
                for platform in ["kalshi", "polymarket"]
            },
            "bbo_files_present": {
                platform: (
                    event_root / f"data/processed/{platform}/bbo.csv.gz"
                ).exists()
                for platform in ["kalshi", "polymarket"]
            },
            "coverage_file": str(output),
            "log_file": str(run_log.path),
        },
        event_root / "data/audits/coverage_manifest.json",
    )
    run_log.info(f"Wrote {len(output_rows)} coverage rows to {output}")


def resolve_event_root(
    event: str | None,
    default_event_root: Path | None,
) -> Path:
    if default_event_root is not None:
        return default_event_root.resolve()
    if event is None:
        raise ValueError("--event is required when using the shared runner")
    return (POLITICAL_ROOT / EVENT_ALIASES[event]).resolve()


def build_parser(require_event: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    if require_event:
        parser.add_argument(
            "--event",
            choices=sorted(EVENT_ALIASES),
            required=True,
            help="Political event family to process.",
        )
    subparsers = parser.add_subparsers(dest="stage", required=True)

    metadata = subparsers.add_parser(
        "build-map", help="Validate official metadata and build the exact market map."
    )
    metadata.add_argument("--request-interval", type=float, default=None)

    trades = subparsers.add_parser(
        "fetch-trades", help="Fetch exact Kalshi and Polymarket trade histories."
    )
    trades.add_argument(
        "--venue", choices=["both", "kalshi", "polymarket"], default="both"
    )
    trades.add_argument(
        "--tier", choices=["all", "primary", "robustness"], default="all"
    )
    trades.add_argument("--max-markets", type=int, default=0)
    trades.add_argument("--request-interval", type=float, default=None)
    trades.add_argument("--overwrite-parts", action="store_true")

    bbo = subparsers.add_parser(
        "fetch-bbo", help="Fetch historical BBO/order-book snapshots from Predexon."
    )
    bbo.add_argument(
        "--venue", choices=["both", "kalshi", "polymarket"], default="both"
    )
    bbo.add_argument(
        "--tier", choices=["all", "primary", "robustness"], default="all"
    )
    bbo.add_argument("--max-markets", type=int, default=0)
    bbo.add_argument("--request-interval", type=float, default=None)
    bbo.add_argument("--overwrite-parts", action="store_true")

    full = subparsers.add_parser(
        "run-all",
        help="Build the map, fetch data, audit it, and run offline analysis.",
    )
    full.add_argument(
        "--venue", choices=["both", "kalshi", "polymarket"], default="both"
    )
    full.add_argument(
        "--tier", choices=["all", "primary", "robustness"], default="all"
    )
    full.add_argument("--max-markets", type=int, default=0)
    full.add_argument("--request-interval", type=float, default=None)
    full.add_argument("--overwrite-parts", action="store_true")
    full.add_argument(
        "--price-impact-sizes",
        type=int,
        nargs="*",
        default=[100, 500, 1000],
    )

    analyze = subparsers.add_parser(
        "analyze",
        help="Build the balanced hourly panel and offline market-quality outputs.",
    )
    analyze.add_argument(
        "--tier", choices=["all", "primary", "robustness"], default="all"
    )
    analyze.add_argument("--max-markets", type=int, default=0)
    analyze.add_argument(
        "--price-impact-sizes",
        type=int,
        nargs="*",
        default=[100, 500, 1000],
    )

    subparsers.add_parser(
        "audit", help="Summarize pre/post trade and usable-BBO coverage."
    )
    return parser


def main(default_event_root: Path | None = None) -> None:
    parser = build_parser(require_event=default_event_root is None)
    args = parser.parse_args()
    event_root = resolve_event_root(getattr(args, "event", None), default_event_root)
    config = load_config(event_root)
    stage = str(args.stage).replace("-", "_")

    def operation(run_log: RunLog) -> None:
        if args.stage == "build-map":
            build_market_map(
                event_root,
                request_interval=args.request_interval,
                run_log=run_log,
            )
        elif args.stage == "fetch-trades":
            fetch_trades(
                event_root,
                venue=args.venue,
                tier=args.tier,
                max_markets=args.max_markets,
                request_interval=args.request_interval,
                overwrite_parts=args.overwrite_parts,
                run_log=run_log,
            )
        elif args.stage == "fetch-bbo":
            fetch_bbo(
                event_root,
                venue=args.venue,
                tier=args.tier,
                max_markets=args.max_markets,
                request_interval=args.request_interval,
                overwrite_parts=args.overwrite_parts,
                run_log=run_log,
            )
        elif args.stage == "run-all":
            if args.venue != "both":
                raise ValueError(
                    "run-all requires --venue both because paired analysis "
                    "needs both platforms; use the individual fetch stages "
                    "for a single-venue download"
                )
            build_market_map(
                event_root,
                request_interval=args.request_interval,
                run_log=run_log,
            )
            fetch_trades(
                event_root,
                venue=args.venue,
                tier=args.tier,
                max_markets=args.max_markets,
                request_interval=args.request_interval,
                overwrite_parts=args.overwrite_parts,
                run_log=run_log,
            )
            fetch_bbo(
                event_root,
                venue=args.venue,
                tier=args.tier,
                max_markets=args.max_markets,
                request_interval=args.request_interval,
                overwrite_parts=args.overwrite_parts,
                run_log=run_log,
            )
            audit_coverage(event_root, run_log)
            from analysis import build_analysis_outputs

            if any(value <= 0 for value in args.price_impact_sizes):
                raise ValueError("Price-impact sizes must be positive integers")
            build_analysis_outputs(
                event_root,
                tier=args.tier,
                max_markets=args.max_markets,
                price_impact_sizes=tuple(sorted(set(args.price_impact_sizes))),
                run_log=run_log,
            )
        elif args.stage == "analyze":
            from analysis import build_analysis_outputs

            if any(value <= 0 for value in args.price_impact_sizes):
                raise ValueError("Price-impact sizes must be positive integers")
            build_analysis_outputs(
                event_root,
                tier=args.tier,
                max_markets=args.max_markets,
                price_impact_sizes=tuple(sorted(set(args.price_impact_sizes))),
                run_log=run_log,
            )
        elif args.stage == "audit":
            audit_coverage(event_root, run_log)
        else:
            raise RuntimeError(f"Unsupported stage: {args.stage}")

    run_logged(event_root, f"{config['event_key']}_{stage}", operation)


if __name__ == "__main__":
    main()
