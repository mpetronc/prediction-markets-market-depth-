"""Unauthenticated Kalshi market-data client used by the BTC hourly collector."""

from __future__ import annotations

import os
import random
import time
from typing import Iterator

import requests

from _pipeline_common import RunLog


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class KalshiPublicClient:
    """Rate-limited client for Kalshi's public, read-only REST endpoints."""

    def __init__(
        self,
        request_interval: float | None = None,
        max_attempts: int = 7,
        run_log: RunLog | None = None,
    ):
        if request_interval is None:
            request_interval = float(
                os.getenv("KALSHI_PUBLIC_REQUEST_INTERVAL_SECONDS", "0.12")
            )
        self.request_interval = max(0.0, request_interval)
        self.max_attempts = max_attempts
        self.run_log = run_log
        self.request_count = 0
        self.retry_count = 0
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Polymarket-vs-Kalshi-research/1.0"}
        )
        self._last_request_at = 0.0
        if self.run_log:
            self.run_log.info(
                "Kalshi public client initialized "
                f"base_url={BASE_URL} authentication=none "
                f"request_interval_seconds={self.request_interval:.3f} "
                f"max_attempts={self.max_attempts}"
            )

    def _throttle(self) -> None:
        remaining = self.request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def get_json(
        self,
        endpoint: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> dict | None:
        """Return a JSON object, optionally mapping HTTP 404 to ``None``."""

        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                self.request_count += 1
                response = self.session.get(
                    url,
                    params=params or {},
                    timeout=(10, 90),
                )
                self._last_request_at = time.monotonic()

                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError("Kalshi response was not a JSON object")
                    return payload
                if response.status_code == 404 and allow_not_found:
                    return None

                message = response.text[:500].replace("\n", " ")
                last_error = RuntimeError(
                    f"Kalshi HTTP {response.status_code}: {message}"
                )
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    raise last_error
                delay = 0.5 * (2 ** (attempt - 1))
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if (
                    isinstance(exc, RuntimeError)
                    and "Kalshi HTTP 4" in str(exc)
                    and "Kalshi HTTP 429" not in str(exc)
                ):
                    raise
                delay = 0.5 * (2 ** (attempt - 1))

            if attempt < self.max_attempts:
                self.retry_count += 1
                wait_seconds = delay + random.uniform(0.0, 0.25)
                if self.run_log:
                    self.run_log.warning(
                        f"Kalshi retry endpoint=/{endpoint.lstrip('/')} "
                        f"attempt={attempt}/{self.max_attempts} "
                        f"wait_seconds={wait_seconds:.2f} reason={last_error}"
                    )
                time.sleep(wait_seconds)

        raise RuntimeError(
            f"Kalshi request failed after {self.max_attempts} attempts: {last_error}"
        )

    def cursor_pages(
        self,
        endpoint: str,
        params: dict[str, object],
        item_key: str,
    ) -> Iterator[tuple[int, dict]]:
        """Yield all pages from a Kalshi cursor-paginated endpoint."""

        cursor: str | None = None
        seen: set[str] = set()
        page_number = 1

        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            payload = self.get_json(endpoint, page_params)
            if payload is None or item_key not in payload:
                raise RuntimeError(f"Missing '{item_key}' in response from {endpoint}")
            items = payload[item_key]
            if not isinstance(items, list):
                raise RuntimeError(f"'{item_key}' from {endpoint} was not a list")
            yield page_number, payload

            next_cursor = payload.get("cursor")
            if not next_cursor:
                return
            if not items:
                raise RuntimeError(
                    f"{endpoint} returned a cursor together with an empty page"
                )
            if next_cursor in seen:
                raise RuntimeError(f"{endpoint} returned a repeated cursor")
            seen.add(next_cursor)
            cursor = str(next_cursor)
            page_number += 1


