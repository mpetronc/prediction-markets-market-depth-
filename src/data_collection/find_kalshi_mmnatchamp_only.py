from pathlib import Path
import os
import requests
import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")
BASE_URL = "https://api.predexon.com/v2"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_championship_candidates.csv"

HEADERS = {"x-api-key": API_KEY}

TARGET_TICKERS = {
    "KXMARMAD-26-MICH": {
        "team": "Michigan",
        "opponent": "UConn",
        "game": "Michigan vs UConn",
    },
    "KXMARMAD-26-CONN": {
        "team": "UConn",
        "opponent": "Michigan",
        "game": "Michigan vs UConn",
    },
}


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def get_event(market):
    event = market.get("event")
    if isinstance(event, dict):
        return event
    return {}


def get_ticker(market):
    return clean(market.get("ticker") or market.get("market_ticker"))


def get_title(market):
    return clean(market.get("title") or market.get("subtitle"))


def get_event_ticker(market):
    event = get_event(market)
    return clean(
        market.get("event_ticker")
        or market.get("eventTicker")
        or event.get("ticker")
    )


def get_event_title(market):
    event = get_event(market)
    return clean(
        market.get("event_title")
        or market.get("eventTitle")
        or event.get("title")
    )


def fetch_market_by_ticker(ticker):
    url = f"{BASE_URL}/kalshi/markets"

    response = requests.get(
        url,
        headers=HEADERS,
        params={
            "limit": 100,
            "ticker": ticker,
        },
        timeout=30,
    )

    print("\n==============================")
    print("FETCH TICKER:", ticker)
    print("==============================")
    print("Status:", response.status_code)
    print(response.url)

    if response.status_code != 200:
        print(response.text[:1000])
        return []

    data = response.json()
    markets = data.get("markets", [])

    print("Returned:", len(markets))

    return markets


def flatten_championship_market(market):
    ticker = get_ticker(market)

    meta = TARGET_TICKERS[ticker]

    return {
        "platform": "kalshi",
        "ticker": ticker,
        "event_ticker": get_event_ticker(market),
        "market_id": market.get("market_id") or market.get("id"),
        "title": get_title(market),
        "event_title": get_event_title(market),

        "team": meta["team"],
        "opponent": meta["opponent"],
        "game": meta["game"],

        "away_team": "Michigan",
        "home_team": "UConn",

        "market_source_type": "championship_future_equivalent",
        "game_classification": "both_mm",
        "round": "National Championship",

        "yes_subtitle": market.get("yes_subtitle") or market.get("yesSubtitle"),
        "no_subtitle": market.get("no_subtitle") or market.get("noSubtitle"),

        "status": market.get("status"),
        "result": market.get("result"),

        "open_time": market.get("open_time") or market.get("openTime"),
        "close_time": market.get("close_time") or market.get("closeTime"),
        "expiration_time": market.get("expiration_time") or market.get("expirationTime"),
        "expected_expiration_time": (
            market.get("expected_expiration_time")
            or market.get("expectedExpirationTime")
        ),
        "settlement_time": market.get("settlement_time") or market.get("settlementTime"),
        "determination_time": (
            market.get("determination_time")
            or market.get("determinationTime")
        ),

        "last_price": market.get("last_price") or market.get("lastPrice"),
        "volume": market.get("volume"),
        "open_interest": market.get("open_interest") or market.get("openInterest"),
        "dollar_volume": market.get("dollar_volume") or market.get("dollarVolume"),
        "dollar_open_interest": (
            market.get("dollar_open_interest")
            or market.get("dollarOpenInterest")
        ),

        "yes_bid": market.get("yes_bid") or market.get("yesBid"),
        "yes_ask": market.get("yes_ask") or market.get("yesAsk"),
        "no_bid": market.get("no_bid") or market.get("noBid"),
        "no_ask": market.get("no_ask") or market.get("noAsk"),
    }


def main():
    if not API_KEY:
        raise RuntimeError("Missing PREDX_API_KEY in .env")

    rows = []

    for ticker in TARGET_TICKERS:
        markets = fetch_market_by_ticker(ticker)

        exact_matches = [
            market for market in markets
            if get_ticker(market) == ticker
        ]

        if not exact_matches:
            print("WARNING: No exact match found for", ticker)
            continue

        for market in exact_matches:
            row = flatten_championship_market(market)
            rows.append(row)

            print("FOUND:")
            print("ticker:", row["ticker"])
            print("event_ticker:", row["event_ticker"])
            print("title:", row["title"])
            print("event_title:", row["event_title"])
            print("team:", row["team"])
            print("opponent:", row["opponent"])
            print("market_source_type:", row["market_source_type"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.drop_duplicates(subset=["ticker"])
        df = df.sort_values(["ticker"])

    df.to_csv(OUT_PATH, index=False)

    print("\nSaved:", OUT_PATH)
    print("Total championship rows:", len(df))

    if not df.empty:
        print("\nChampionship markets:")
        visible_cols = [
            "ticker",
            "event_ticker",
            "title",
            "event_title",
            "team",
            "opponent",
            "market_source_type",
            "close_time",
            "status",
            "result",
        ]
        print(df[visible_cols].to_string(index=False))


if __name__ == "__main__":
    main()