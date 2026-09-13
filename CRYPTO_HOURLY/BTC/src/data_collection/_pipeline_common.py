"""Shared, collection-only helpers for the BTC Predexon pilot."""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

import requests
from dotenv import load_dotenv


ASSET_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(REPO_ROOT / ".env")

API_KEY_ENV = "PREDX_API_KEY"
BASE_URL = "https://api.predexon.com/v2"

WINDOW_START = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
TREATMENT_TIME = datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc)

INCIDENT_START = datetime(2026, 8, 17, 12, 45, tzinfo=timezone.utc)
INCIDENT_END = datetime(2026, 8, 17, 15, 18, tzinfo=timezone.utc)


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
    """A timestamped file log mirrored to the terminal."""

    def __init__(self, stage: str, log_root: Path | None = None):
        self.stage = safe_filename(stage)
        self.started_at = time.monotonic()
        root = log_root or ASSET_ROOT / "data/logs"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = root / f"{stamp}_{self.stage}.log"
        logger_name = f"predexon.{ASSET_ROOT.parent.name}.{ASSET_ROOT.name}.{stage}.{stamp}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)sZ | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        file_handler = logging.FileHandler(self.path, encoding="utf-8")
        terminal_handler = logging.StreamHandler(sys.stdout)
        file_handler.setFormatter(formatter)
        terminal_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(terminal_handler)
        self.info(f"Run started stage={self.stage} log={self.path}")

    def info(self, message: str) -> None:
        self.logger.info(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

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


def run_logged(stage: str, operation: Callable[[RunLog], None]) -> None:
    """Run a pipeline stage with durable success, failure, and interrupt records."""

    run_log = RunLog(stage)
    try:
        operation(run_log)
    except KeyboardInterrupt:
        run_log.warning(
            f"Run interrupted stage={run_log.stage} "
            f"elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
        raise
    except Exception:
        run_log.exception(
            f"Run failed stage={run_log.stage} "
            f"elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
        raise
    else:
        run_log.info(
            f"Run completed stage={run_log.stage} "
            f"elapsed={format_duration(time.monotonic() - run_log.started_at)}"
        )
    finally:
        run_log.close()


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: str | datetime) -> str:
    return parse_utc(value).isoformat().replace("+00:00", "Z")


def epoch_seconds(value: str | datetime) -> int:
    return int(parse_utc(value).timestamp())


def epoch_milliseconds(value: str | datetime) -> int:
    return int(parse_utc(value).timestamp() * 1_000)


def treatment_period(timestamp_seconds: int | float) -> str:
    return "pre" if float(timestamp_seconds) < TREATMENT_TIME.timestamp() else "post"


def in_incident_window(timestamp_seconds: int | float) -> bool:
    value = float(timestamp_seconds)
    return INCIDENT_START.timestamp() <= value < INCIDENT_END.timestamp()


def safe_filename(value: object) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return clean or "unknown"


def batched(values: Iterable[str], size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def write_json_atomic(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def write_json_gzip_atomic(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(path)


def write_csv_atomic(rows: Iterable[dict], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required pipeline input does not exist: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class PredexonClient:
    """Rate-limited client with retries and pagination-loop protection."""

    def __init__(
        self,
        request_interval: float | None = None,
        max_attempts: int = 0,
        run_log: RunLog | None = None,
    ):
        api_key = os.getenv(API_KEY_ENV)
        if not api_key:
            raise RuntimeError(
                f"Missing {API_KEY_ENV} in the environment or {REPO_ROOT / '.env'}"
            )
        if request_interval is None:
            request_interval = float(
                os.getenv("PREDEXON_REQUEST_INTERVAL_SECONDS", "0.012")
            )
        self.request_interval = max(0.0, request_interval)
        configured_attempts = int(os.getenv("PREDEXON_MAX_ATTEMPTS", str(max_attempts)))
        self.max_attempts = max(0, configured_attempts)
        self.max_retry_wait = max(
            1.0, float(os.getenv("PREDEXON_MAX_RETRY_WAIT_SECONDS", "60"))
        )
        self.run_log = run_log
        self.request_count = 0
        self.retry_count = 0
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key})
        self._last_request_at = 0.0
        if self.run_log:
            self.run_log.info(
                "Predexon client initialized "
                f"request_interval_seconds={self.request_interval:.3f} "
                f"max_attempts={'unlimited' if self.max_attempts == 0 else self.max_attempts} "
                f"max_retry_wait_seconds={self.max_retry_wait:.1f}"
            )

    def _throttle(self) -> None:
        remaining = self.request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def get_json(
        self,
        endpoint: str,
        params: dict | list[tuple[str, object]],
    ) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None

        attempt = 1
        while self.max_attempts == 0 or attempt <= self.max_attempts:
            self._throttle()
            try:
                self.request_count += 1
                response = self.session.get(url, params=params, timeout=(10, 90))
                self._last_request_at = time.monotonic()

                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError("Predexon response was not a JSON object")
                    return payload

                message = response.text[:500].replace("\n", " ")
                last_error = RuntimeError(
                    f"Predexon HTTP {response.status_code}: {message}"
                )
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    raise last_error

                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after
                    else 0.75 * (2 ** min(attempt - 1, 10))
                )
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if (
                    isinstance(exc, RuntimeError)
                    and "HTTP 4" in str(exc)
                    and "HTTP 429" not in str(exc)
                ):
                    raise
                delay = 0.75 * (2 ** min(attempt - 1, 10))

            if self.max_attempts == 0 or attempt < self.max_attempts:
                self.retry_count += 1
                wait_seconds = min(self.max_retry_wait, delay) + random.uniform(0.0, 0.25)
                if self.run_log:
                    self.run_log.warning(
                        f"Predexon retry endpoint=/{endpoint.lstrip('/')} "
                        f"attempt={attempt}/"
                        f"{'unlimited' if self.max_attempts == 0 else self.max_attempts} "
                        f"wait_seconds={wait_seconds:.2f} reason={last_error}"
                    )
                time.sleep(wait_seconds)
            attempt += 1

        raise RuntimeError(
            f"Predexon request failed after {self.max_attempts} attempts: {last_error}"
        )

    def cursor_pages(
        self,
        endpoint: str,
        params: dict | list[tuple[str, object]],
        item_key: str,
    ) -> Iterator[tuple[int, dict]]:
        cursor: str | None = None
        seen: set[str] = set()
        page_number = 1

        while True:
            page_params = list(params) if isinstance(params, list) else dict(params)
            if cursor:
                if isinstance(page_params, list):
                    page_params.append(("pagination_key", cursor))
                else:
                    page_params["pagination_key"] = cursor
            payload = self.get_json(endpoint, page_params)
            if item_key not in payload:
                raise RuntimeError(f"Missing '{item_key}' in response from {endpoint}")
            yield page_number, payload

            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                return
            next_cursor = pagination.get("pagination_key")
            if not next_cursor:
                raise RuntimeError(f"{endpoint} says has_more but returned no cursor")
            if next_cursor in seen:
                raise RuntimeError(f"{endpoint} returned a repeated pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
            page_number += 1

    def offset_pages(
        self,
        endpoint: str,
        params: dict,
        item_key: str,
    ) -> Iterator[tuple[int, dict]]:
        offset = 0
        page_number = 1

        while True:
            page_params = dict(params)
            page_params["offset"] = offset
            payload = self.get_json(endpoint, page_params)
            items = payload.get(item_key)
            if items is None:
                raise RuntimeError(f"Missing '{item_key}' in response from {endpoint}")
            yield page_number, payload

            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                return
            if not items:
                raise RuntimeError(f"{endpoint} says has_more but returned an empty page")
            offset += len(items)
            page_number += 1
