from pathlib import Path
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone


# File location:
# src/data_collection/tests/kalshi/test_predexon_orderbook.py
#
# parents[4] goes back to project root:
# Polymarket-vs-Kalshi-market-depth-
PROJECT_ROOT = Path(__file__).resolve().parents[4]

load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")

if not API_KEY:
    raise ValueError("Missing PREDX_API_KEY in .env")

BASE_URL = "https://api.predexon.com/v2"


def unix_milliseconds(date_string):
    dt = datetime.fromisoformat(date_string).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def test_kalshi_orderbook(ticker, start_date, end_date):
    url = f"{BASE_URL}/kalshi/orderbooks"

    params = {
        "ticker": ticker,
        "start_time": unix_milliseconds(start_date),
        "end_time": unix_milliseconds(end_date),
        "limit": 10,
    }

    headers = {
        "x-api-key": API_KEY,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    print("\n" + "=" * 100)
    print("TEST KALSHI ORDERBOOK")
    print("Ticker:", ticker)
    print("Start:", start_date)
    print("End:", end_date)
    print("Status:", response.status_code)
    print("URL:", response.url)
    print("Response:")
    print(response.text[:5000])

    return response


def main():
    test_kalshi_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU-AKR",
        start_date="2026-03-15T00:00:00",
        end_date="2026-03-22T00:00:00",
    )

    test_kalshi_orderbook(
        ticker="KXNCAAMBGAME-26MAR20AKRTTU-TTU",
        start_date="2026-03-15T00:00:00",
        end_date="2026-03-22T00:00:00",
    )


if __name__ == "__main__":
    main()