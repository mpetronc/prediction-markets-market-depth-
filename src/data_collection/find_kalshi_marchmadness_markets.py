from pathlib import Path
import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")
BASE_URL = "https://api.predexon.com/v2"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_candidate_markets.csv"
REMOVED_MIXED_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_removed_mixed_games.csv"
REMOVED_NEITHER_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_removed_neither_games.csv"

HEADERS = {
    "x-api-key": API_KEY,
}

START_DATE = "2026-03-17"
END_DATE = "2026-04-07"

SERIES_TICKER = "KXNCAAMBGAME"

STOP_AFTER_EMPTY_FINAL_PAGES = 10


MM_TEAMS = {
    "Akron",
    "Alabama",
    "Arizona",
    "Arkansas",
    "BYU",
    "Cal Baptist",
    "Clemson",
    "Duke",
    "Florida",
    "Furman",
    "Georgia",
    "Gonzaga",
    "Hawaii",
    "High Point",
    "Hofstra",
    "Houston",
    "Howard",
    "Idaho",
    "Illinois",
    "Iowa",
    "Iowa St.",
    "Kansas",
    "Kennesaw St.",
    "Kentucky",
    "Lehigh",
    "Long Island",
    "Louisville",
    "McNeese",
    "Miami (FL)",
    "Miami (Ohio)",
    "Michigan",
    "Michigan St.",
    "Missouri",
    "NC State",
    "Nebraska",
    "North Carolina",
    "North Dakota St.",
    "Northern Iowa",
    "Ohio St.",
    "Penn",
    "Prairie View A&M",
    "Purdue",
    "Queens (N.C.)",
    "Saint Louis",
    "Saint Mary's",
    "Santa Clara",
    "Siena",
    "SMU",
    "South Florida",
    "St. John's",
    "TCU",
    "Tennessee",
    "Tennessee St.",
    "Texas",
    "Texas A&M",
    "Texas Tech",
    "Troy",
    "UCF",
    "UCLA",
    "UConn",
    "UMBC",
    "Utah St.",
    "Vanderbilt",
    "VCU",
    "Villanova",
    "Virginia",
    "Wisconsin",
    "Wright St.",
}


TEAM_ALIASES = {
    "California Baptist": "Cal Baptist",
    "Cal Baptist": "Cal Baptist",

    "Hawai'i": "Hawaii",
    "Hawaii": "Hawaii",

    "Iowa State": "Iowa St.",
    "Iowa St.": "Iowa St.",

    "LIU": "Long Island",
    "Long Island": "Long Island",

    "Miami (OH)": "Miami (Ohio)",
    "Miami (Ohio)": "Miami (Ohio)",

    "Michigan State": "Michigan St.",
    "Michigan St.": "Michigan St.",

    "North Carolina St.": "NC State",
        "NC St.": "NC State",
        "NC State": "NC State",

    "North Dakota State": "North Dakota St.",
    "North Dakota St.": "North Dakota St.",

    "Ohio State": "Ohio St.",
    "Ohio St.": "Ohio St.",

    "Prairie View": "Prairie View A&M",
    "Prairie View A&M": "Prairie View A&M",

    "Queens University": "Queens (N.C.)",
    "Queens (N.C.)": "Queens (N.C.)",

    "Saint Marys": "Saint Mary's",
    "Saint Mary's": "Saint Mary's",

    "St John's": "St. John's",
    "St. Johns": "St. John's",
    "St. John's": "St. John's",

    "Texas A and M": "Texas A&M",
    "Texas A&M": "Texas A&M",

    "Texas Tech": "Texas Tech",

    "Tennessee State": "Tennessee St.",
    "Tennessee St.": "Tennessee St.",

    "Utah State": "Utah St.",
    "Utah St.": "Utah St.",

    "Wright State": "Wright St.",
    "Wright St.": "Wright St.",
}


def normalize_team_name(team_name: str) -> str:
    if team_name is None:
        return ""

    name = str(team_name).strip()

    name = name.replace("’", "'")
    name = name.replace("  ", " ")

    return TEAM_ALIASES.get(name, name)


def parse_teams_from_event_title(event_title: str) -> tuple[str, str]:
    title = str(event_title).strip()

    if " at " not in title:
        return "", ""

    away_team, home_team = title.split(" at ", 1)

    return away_team.strip(), home_team.strip()


def classify_game(event_title: str) -> tuple[str, str, str]:
    away_team_raw, home_team_raw = parse_teams_from_event_title(event_title)

    away_team = normalize_team_name(away_team_raw)
    home_team = normalize_team_name(home_team_raw)

    away_is_mm = away_team in MM_TEAMS
    home_is_mm = home_team in MM_TEAMS

    if away_is_mm and home_is_mm:
        classification = "both_mm"
    elif away_is_mm or home_is_mm:
        classification = "mixed_one_mm_one_not"
    else:
        classification = "neither_mm"

    return away_team, home_team, classification


def is_team_win_march_madness_market(market: dict) -> bool:
    ticker = str(market.get("ticker", ""))
    title = str(market.get("title", "")).lower()
    close_time = str(market.get("close_time", ""))

    event = market.get("event") or {}
    event_title = str(event.get("title", ""))

    away_team, home_team, game_classification = classify_game(event_title)

    is_ncaa_mens_game_market = ticker.startswith("KXNCAAMBGAME")
    is_winner_market = "winner" in title
    is_in_march_madness_window = START_DATE <= close_time[:10] <= END_DATE
    both_teams_are_march_madness_teams = game_classification == "both_mm"

    return (
        is_ncaa_mens_game_market
        and is_winner_market
        and is_in_march_madness_window
        and both_teams_are_march_madness_teams
    )


def flatten_market(market: dict) -> dict:
    event = market.get("event") or {}

    event_title = event.get("title")
    away_team, home_team, game_classification = classify_game(event_title)

    return {
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "market_id": market.get("market_id"),

        "title": market.get("title"),
        "yes_subtitle": market.get("yes_subtitle"),
        "no_subtitle": market.get("no_subtitle"),

        "status": market.get("status"),
        "result": market.get("result"),

        "open_time": market.get("open_time"),
        "close_time": market.get("close_time"),
        "expected_expiration_time": market.get("expected_expiration_time"),
        "settlement_time": market.get("settlement_time"),
        "determination_time": market.get("determination_time"),

        "last_price": market.get("last_price"),
        "volume": market.get("volume"),
        "open_interest": market.get("open_interest"),
        "dollar_volume": market.get("dollar_volume"),
        "dollar_open_interest": market.get("dollar_open_interest"),

        "event_series_ticker": event.get("series_ticker"),
        "event_title": event_title,
        "event_subtitle": event.get("subtitle"),

        "away_team": away_team,
        "home_team": home_team,
        "game_classification": game_classification,
    }


def fetch_markets_page(pagination_key: str | None = None) -> dict:
    params = {
        "limit": 100,
        "series_ticker": SERIES_TICKER,
    }

    if pagination_key:
        params["pagination_key"] = pagination_key

    response = requests.get(
        f"{BASE_URL}/kalshi/markets",
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    print("Status:", response.status_code)
    print(response.url)

    response.raise_for_status()
    return response.json()


def save_diagnostics(all_seen_rows: list[dict]) -> None:
    if not all_seen_rows:
        return

    seen_df = pd.DataFrame(all_seen_rows)

    game_cols = [
        "event_ticker",
        "event_title",
        "away_team",
        "home_team",
        "game_classification",
    ]

    games_df = seen_df[game_cols].drop_duplicates(subset=["event_ticker"])

    counts = games_df["game_classification"].value_counts()

    print("\nGame classification counts seen during pull:")
    print(counts)

    mixed_df = games_df[games_df["game_classification"] == "mixed_one_mm_one_not"]
    neither_df = games_df[games_df["game_classification"] == "neither_mm"]

    REMOVED_MIXED_PATH.parent.mkdir(parents=True, exist_ok=True)
    mixed_df.to_csv(REMOVED_MIXED_PATH, index=False)
    neither_df.to_csv(REMOVED_NEITHER_PATH, index=False)

    print("\nSaved mixed-game diagnostics:", REMOVED_MIXED_PATH)
    print("Saved neither-MM diagnostics:", REMOVED_NEITHER_PATH)

    if not mixed_df.empty:
        print("\nFirst 20 mixed games removed:")
        print(mixed_df.head(20).to_string(index=False))


def main():
    if not API_KEY:
        raise ValueError("Missing PREDX_API_KEY in .env")

    all_candidates = []
    all_seen_rows = []

    pagination_key = None
    page = 1
    empty_final_candidate_pages = 0

    while True:
        print(f"\nFetching page {page}...")

        data = fetch_markets_page(pagination_key)
        markets = data.get("markets", [])

        print("Markets returned:", len(markets))

        page_seen_rows = [flatten_market(market) for market in markets]
        all_seen_rows.extend(page_seen_rows)

        page_candidates = [
            flatten_market(market)
            for market in markets
            if is_team_win_march_madness_market(market)
        ]

        print("Valid March Madness team-win markets on page:", len(page_candidates))

        if len(page_candidates) == 0:
            empty_final_candidate_pages += 1
        else:
            empty_final_candidate_pages = 0

        all_candidates.extend(page_candidates)

        pagination = data.get("pagination", {})
        has_more = pagination.get("has_more", False)
        pagination_key = pagination.get("pagination_key")

        if not has_more or not pagination_key:
            break

        if empty_final_candidate_pages >= STOP_AFTER_EMPTY_FINAL_PAGES:
            print(f"Stopping after {STOP_AFTER_EMPTY_FINAL_PAGES} empty pages in a row.")
            break

        page += 1
        time.sleep(0.05)

    df = pd.DataFrame(all_candidates)

    if not df.empty:
        df = df.drop_duplicates(subset=["ticker"])
        df = df.sort_values(["close_time", "event_ticker", "ticker"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    print("\nSaved:", OUT_PATH)
    print("Total valid Kalshi candidates:", len(df))

    if not df.empty:
        print("\nUnique games:", df["event_ticker"].nunique())
        print("\nFirst 80 candidate markets:")
        print(df.head(80).to_string(index=False))
    else:
        print("No valid Kalshi March Madness team-win markets found.")

    save_diagnostics(all_seen_rows)


if __name__ == "__main__":
    main()