from pathlib import Path
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone


# File location:
# src/data_collection/tests/polymarket/test_predexon_orderbook.py
#
# parents[4] goes back to project root:
# Polymarket-vs-Kalshi-market-depth-
PROJECT_ROOT = Path(__file__).resolve().parents[4]

load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")

if not API_KEY:
    raise ValueError("Missing PREDX_API_KEY in .env")

BASE_URL = "https://api.predexon.com/v2"


def unix_seconds(date_string):
    dt = datetime.fromisoformat(date_string).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def unix_milliseconds(date_string):
    dt = datetime.fromisoformat(date_string).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def test_polymarket_orderbook(params, label):
    url = f"{BASE_URL}/polymarket/orderbooks"

    headers = {
        "x-api-key": API_KEY,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    print("\n" + "=" * 100)
    print(label)
    print("Status:", response.status_code)
    print("URL:", response.url)
    print("Response:")
    print(response.text[:5000])

    return response


def main():
    # From MM_GAME_001: Akron vs Texas Tech
    condition_id = "0x04c109837e9c83aa410e4a1bfa32d5574b05fafd0a432514965c03a20e2511a6"

    token_id_1 = "105015751806411093867366456953350999512252519399462229009921348002493556208921"
    token_id_2 = "79308286211285981481697518081271815960844799414039636458581868757032388361925"

    market_id = "1602165"

    start_ms = unix_milliseconds("2026-03-15T00:00:00")
    end_ms = unix_milliseconds("2026-03-22T00:00:00")

    start_s = unix_seconds("2026-03-15T00:00:00")
    end_s = unix_seconds("2026-03-22T00:00:00")

    # Main expected test:
    # Docs indicate Polymarket orderbook history is token-level.
    test_polymarket_orderbook(
        params={
            "token_id": token_id_1,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 10,
        },
        label="TEST 1: token_id_1 with milliseconds",
    )

    test_polymarket_orderbook(
        params={
            "token_id": token_id_2,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 10,
        },
        label="TEST 2: token_id_2 with milliseconds",
    )

    # Fallback tests in case Predexon uses a different parameter shape.
    test_polymarket_orderbook(
        params={
            "condition_id": condition_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 10,
        },
        label="TEST 3: condition_id with milliseconds",
    )

    test_polymarket_orderbook(
        params={
            "market_id": market_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 10,
        },
        label="TEST 4: market_id with milliseconds",
    )

    # Time-format fallback tests.
    # If milliseconds return empty/422, these tell us whether Polymarket orderbooks use seconds.
    test_polymarket_orderbook(
        params={
            "token_id": token_id_1,
            "start_time": start_s,
            "end_time": end_s,
            "limit": 10,
        },
        label="TEST 5: token_id_1 with seconds",
    )

    test_polymarket_orderbook(
        params={
            "token_id": token_id_2,
            "start_time": start_s,
            "end_time": end_s,
            "limit": 10,
        },
        label="TEST 6: token_id_2 with seconds",
    )


if __name__ == "__main__":
    main()