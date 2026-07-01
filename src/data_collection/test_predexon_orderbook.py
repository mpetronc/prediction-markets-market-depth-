from pathlib import Path
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")

if not API_KEY:
    raise ValueError("Missing PREDX_API_KEY in .env")

BASE_URL = "https://api.predexon.com/v2"

def unix_milliseconds(date_string):
    dt = datetime.fromisoformat(date_string).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def test_orderbook(ticker, start_date, end_date):
    url = f"{BASE_URL}/kalshi/orderbooks"

    params = {
        "ticker": ticker,
        "start_time": unix_milliseconds(start_date),
        "end_time": unix_milliseconds(end_date),
        "limit": 10,
    }

    headers = {
        "x-api-key": API_KEY
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    print("\n" + "=" * 80)
    print("TEST TICKER:", ticker)
    print("START:", start_date)
    print("END:", end_date)
    print("Status:", response.status_code)
    print("URL:", response.url)
    print("Response:")
    print(response.text[:5000])

    return response


def main():
    # Test 1: team contract ticker, wider window
    test_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU-AKR",
        start_date="2026-03-15T00:00:00",
        end_date="2026-03-22T00:00:00",
    )

    # Test 2: other team contract ticker, wider window
    test_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU-TTU",
        start_date="2026-03-15T00:00:00",
        end_date="2026-03-22T00:00:00",
    )

    # Test 3: event ticker instead of team contract ticker
    test_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU",
        start_date="2026-03-15T00:00:00",
        end_date="2026-03-22T00:00:00",
    )

    # Test 4: very wide window
    test_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU-AKR",
        start_date="2026-03-01T00:00:00",
        end_date="2026-04-01T00:00:00",
    )


if __name__ == "__main__":
    main()