from pathlib import Path
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone


PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")

if not API_KEY:
    raise ValueError("Missing PREDX_API_KEY in .env")

BASE_URL = "https://api.predexon.com/v2"


def unix_seconds(date_string):
    dt = datetime.fromisoformat(date_string).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def test_kalshi_trades(params, label):
    url = f"{BASE_URL}/kalshi/trades"

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
    start_time = unix_seconds("2026-03-15T00:00:00")
    end_time = unix_seconds("2026-03-22T00:00:00")

    # Test 1: team contract ticker
    test_kalshi_trades(
        params={
            "ticker": "KXNCAAMBGAME-26MAR20AKRTTU-AKR",
            "start_time": start_time,
            "end_time": end_time,
            "limit": 10,
            "order": "asc",
        },
        label="TEST 1: team ticker AKR, seconds",
    )

    # Test 2: other team contract ticker
    test_kalshi_trades(
        params={
            "ticker": "KXNCAAMBGAME-26MAR20AKRTTU-TTU",
            "start_time": start_time,
            "end_time": end_time,
            "limit": 10,
            "order": "asc",
        },
        label="TEST 2: team ticker TTU, seconds",
    )

    # Test 3: event ticker prefix
    test_kalshi_trades(
        params={
            "event_ticker": "KXNCAAMBGAME-26MAR20AKRTTU",
            "start_time": start_time,
            "end_time": end_time,
            "limit": 10,
            "order": "asc",
        },
        label="TEST 3: event_ticker, seconds",
    )

    # Test 4: event ticker with wider window
    test_kalshi_trades(
        params={
            "event_ticker": "KXNCAAMBGAME-26MAR20AKRTTU",
            "start_time": unix_seconds("2026-03-01T00:00:00"),
            "end_time": unix_seconds("2026-04-10T00:00:00"),
            "limit": 10,
            "order": "asc",
        },
        label="TEST 4: event_ticker, wider window, seconds",
    )


if __name__ == "__main__":
    main()