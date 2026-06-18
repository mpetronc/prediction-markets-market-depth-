import pandas as pd
from pathlib import Path

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 120)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EVENTS_PATH = PROJECT_ROOT / "data/raw/polymarket/polymarket_events.csv"
MARKETS_PATH = PROJECT_ROOT / "data/raw/polymarket/polymarket_markets.csv"

OUTPUT_DIR = PROJECT_ROOT / "data/processed/polymarket"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_OUT = OUTPUT_DIR / "ncaa_cbb_events_debug.csv"
MARKETS_OUT = OUTPUT_DIR / "ncaa_cbb_markets_debug.csv"


def main():
    events = pd.read_csv(EVENTS_PATH, low_memory=False)
    markets = pd.read_csv(MARKETS_PATH, low_memory=False)

    ncaa_events = events[
        events["seriesSlug"].astype(str).str.lower().eq("ncaa-cbb")
    ].copy()

    print(f"NCAA CBB events found: {len(ncaa_events)}")

    date_columns = [
        "startDate",
        "startTime",
        "eventDate",
        "endDate",
        "createdAt",
        "closedTime",
        "updatedAt",
    ]

    print("\nDate ranges inside NCAA CBB events:")
    for col in date_columns:
        if col in ncaa_events.columns:
            parsed = pd.to_datetime(ncaa_events[col], errors="coerce", utc=True)
            print(f"{col}:")
            print("  min:", parsed.min())
            print("  max:", parsed.max())
            print("  non-empty:", parsed.notna().sum())

    event_cols = [
        col for col in [
            "id",
            "title",
            "slug",
            "seriesSlug",
            "homeTeamName",
            "awayTeamName",
            "startDate",
            "startTime",
            "eventDate",
            "endDate",
            "volume",
        ]
        if col in ncaa_events.columns
    ]

    print("\nNCAA CBB event preview:")
    print(ncaa_events[event_cols].head(100).to_string(index=False))

    event_ids = set(ncaa_events["id"].astype(str))

    ncaa_markets = markets[
        markets["event_id"].astype(str).isin(event_ids)
    ].copy()

    print(f"\nNCAA CBB linked markets found: {len(ncaa_markets)}")

    market_cols = [
        col for col in [
            "id",
            "conditionId",
            "question",
            "event_title",
            "event_id",
            "gameId",
            "sportsMarketType",
            "clobTokenIds",
            "outcomes",
            "bestBid",
            "bestAsk",
            "volume",
            "volumeClob",
            "gameStartTime",
            "startDate",
            "endDate",
        ]
        if col in ncaa_markets.columns
    ]

    print("\nNCAA CBB market preview:")
    print(ncaa_markets[market_cols].head(100).to_string(index=False))

    ncaa_events.to_csv(EVENTS_OUT, index=False)
    ncaa_markets.to_csv(MARKETS_OUT, index=False)

    print(f"\nSaved NCAA events debug file to {EVENTS_OUT}")
    print(f"Saved NCAA markets debug file to {MARKETS_OUT}")


if __name__ == "__main__":
    main()