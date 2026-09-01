from pathlib import Path
from datetime import datetime, timezone, timedelta
import os
import re
import json
import time
import pandas as pd
import requests
from dotenv import load_dotenv


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")
if not API_KEY:
    raise ValueError("Missing PREDX_API_KEY in .env")

BASE_URL = "https://api.predexon.com/v2"

HEADERS = {
    "x-api-key": API_KEY
}

MARKET_MAP_PATH = PROJECT_ROOT / "data/matched_markets/MM_market_map.csv"

KALSHI_OUT = PROJECT_ROOT / "data/processed/kalshi/bbo.csv"
POLY_OUT = PROJECT_ROOT / "data/processed/polymarket/bbo.csv"

COMPLETED_PATH = PROJECT_ROOT / "data/processed/fetch_bbo_completed_games.txt"

# Fetch all 67 games
START_GAME = 1
END_GAME = 67

# For each game date, fetch from date - 5 days until date + 2 days.
LOOKBACK_DAYS = 5
AFTER_DAYS = 2

LIMIT = 200
REQUEST_SLEEP_SECONDS = 0.10
RETRY_SLEEP_SECONDS = 5

# True = fresh full run.
# False = resume from completed-games file.
RESET_OUTPUT = True


# ============================================================
# HELPERS
# ============================================================

def ensure_dirs():
    KALSHI_OUT.parent.mkdir(parents=True, exist_ok=True)
    POLY_OUT.parent.mkdir(parents=True, exist_ok=True)
    COMPLETED_PATH.parent.mkdir(parents=True, exist_ok=True)


def reset_outputs_if_needed():
    if RESET_OUTPUT:
        for path in [KALSHI_OUT, POLY_OUT, COMPLETED_PATH]:
            if path.exists():
                path.unlink()


def load_completed_games():
    if not COMPLETED_PATH.exists():
        return set()

    with open(COMPLETED_PATH, "r") as f:
        return set(line.strip() for line in f if line.strip())


def mark_game_completed(game_id):
    with open(COMPLETED_PATH, "a") as f:
        f.write(game_id + "\n")


def append_rows_to_csv(rows, path):
    if not rows:
        return

    df = pd.DataFrame(rows)
    write_header = not path.exists()

    df.to_csv(
        path,
        mode="a",
        header=write_header,
        index=False
    )


def parse_game_date(row):
    month_map = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    }

    kalshi_event_ticker = str(row.get("kalshi_event_ticker", ""))

    match = re.search(
        r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
        kalshi_event_ticker
    )

    if match:
        year = 2000 + int(match.group(1))
        month = month_map[match.group(2)]
        day = int(match.group(3))
        return datetime(year, month, day, tzinfo=timezone.utc)

    slug = str(row.get("polymarket_event_slug", ""))
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", slug)

    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        return datetime(year, month, day, tzinfo=timezone.utc)

    raise ValueError(f"Could not parse game date for row: {row.to_dict()}")


def get_window_milliseconds(row):
    game_date = parse_game_date(row)

    start_dt = game_date - timedelta(days=LOOKBACK_DAYS)
    end_dt = game_date + timedelta(days=AFTER_DAYS)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    return start_ms, end_ms


def get_json_with_retries(url, params, max_attempts=3):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=60
            )

            if response.status_code == 200:
                try:
                    return response.json()
                except Exception as e:
                    last_error = e
                    print("JSON parse failed")
                    print("URL:", response.url)
                    print("Response:", response.text[:1000])

            else:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                print(f"Request failed attempt {attempt}/{max_attempts}")
                print("Status:", response.status_code)
                print("URL:", response.url)
                print("Response:", response.text[:1000])

        except Exception as e:
            last_error = e
            print(f"Request exception attempt {attempt}/{max_attempts}: {e}")

        if attempt < max_attempts:
            time.sleep(RETRY_SLEEP_SECONDS * attempt)

    raise RuntimeError(f"Request failed after {max_attempts} attempts: {last_error}")


def fetch_paginated(url, base_params, list_key):
    all_items = []
    pagination_key = None
    page = 1

    while True:
        params = dict(base_params)

        if pagination_key:
            params["pagination_key"] = pagination_key

        print(f"    page {page}...")

        data = get_json_with_retries(url, params)

        items = data.get(list_key, [])
        pagination = data.get("pagination", {})

        all_items.extend(items)

        has_more = pagination.get("has_more", False)
        pagination_key = pagination.get("pagination_key")

        print(f"    got {len(items)} snapshots, total {len(all_items)}, has_more={has_more}")

        if not has_more or not pagination_key:
            break

        page += 1
        time.sleep(REQUEST_SLEEP_SECONDS)

    return all_items


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False)


def normalize_price(value):
    if value is None or value == "":
        return None

    try:
        value = float(value)
    except Exception:
        return None

    if value > 1:
        return value / 100

    return value


def compute_mid(best_bid, best_ask):
    if best_bid is None or best_ask is None:
        return None

    if best_bid == 0 and best_ask == 0:
        return None

    return (best_bid + best_ask) / 2


def get_price_level_price(level):
    if not isinstance(level, dict):
        return None
    return normalize_price(level.get("price"))


def get_price_level_size(level):
    if not isinstance(level, dict):
        return 0

    size = level.get("size", 0)

    try:
        return float(size)
    except Exception:
        return 0


def calculate_best_bid(bids):
    if not bids:
        return None

    prices = [
        get_price_level_price(level)
        for level in bids
        if get_price_level_price(level) is not None
    ]

    if not prices:
        return None

    return max(prices)


def calculate_best_ask(asks):
    if not asks:
        return None

    prices = [
        get_price_level_price(level)
        for level in asks
        if get_price_level_price(level) is not None
    ]

    if not prices:
        return None

    return min(prices)


def calculate_depth(levels):
    if not levels:
        return 0

    return sum(get_price_level_size(level) for level in levels)


# ============================================================
# KALSHI BBO
# ============================================================

def normalize_kalshi_snapshot(game_id, team, ticker, snapshot):
    best_bid = normalize_price(snapshot.get("best_bid"))
    best_ask = normalize_price(snapshot.get("best_ask"))

    bid_depth = snapshot.get("bid_depth")
    ask_depth = snapshot.get("ask_depth")

    try:
        bid_depth = float(bid_depth)
    except Exception:
        bid_depth = 0

    try:
        ask_depth = float(ask_depth)
    except Exception:
        ask_depth = 0

    return {
        "game_id": game_id,
        "platform": "kalshi",
        "team": team,
        "instrument_id": ticker,
        "timestamp": snapshot.get("timestamp"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": compute_mid(best_bid, best_ask),
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "sequence": snapshot.get("sequence"),
        "raw_bids": safe_json(snapshot.get("yes_bids", [])),
        "raw_asks": safe_json(snapshot.get("yes_asks", [])),
    }


def fetch_kalshi_orderbook(game_id, team, ticker, start_ms, end_ms):
    if not ticker or pd.isna(ticker):
        return []

    url = f"{BASE_URL}/kalshi/orderbooks"

    params = {
        "ticker": ticker,
        "start_time": start_ms,
        "end_time": end_ms,
        "limit": LIMIT,
    }

    print(f"Fetching Kalshi BBO: {game_id} | {team} | {ticker}")

    snapshots = fetch_paginated(url, params, "snapshots")

    rows = [
        normalize_kalshi_snapshot(game_id, team, ticker, snapshot)
        for snapshot in snapshots
    ]

    print(f"  Kalshi snapshots for instrument: {len(rows)}")

    return rows


# ============================================================
# POLYMARKET BBO
# ============================================================

def normalize_polymarket_snapshot(game_id, team, token_id, snapshot):
    bids = snapshot.get("bids", [])
    asks = snapshot.get("asks", [])

    best_bid = snapshot.get("best_bid")
    best_ask = snapshot.get("best_ask")

    if best_bid is None:
        best_bid = calculate_best_bid(bids)
    else:
        best_bid = normalize_price(best_bid)

    if best_ask is None:
        best_ask = calculate_best_ask(asks)
    else:
        best_ask = normalize_price(best_ask)

    bid_depth = snapshot.get("bid_depth")
    ask_depth = snapshot.get("ask_depth")

    if bid_depth is None:
        bid_depth = calculate_depth(bids)
    else:
        try:
            bid_depth = float(bid_depth)
        except Exception:
            bid_depth = 0

    if ask_depth is None:
        ask_depth = calculate_depth(asks)
    else:
        try:
            ask_depth = float(ask_depth)
        except Exception:
            ask_depth = 0

    return {
        "game_id": game_id,
        "platform": "polymarket",
        "team": team,
        "instrument_id": str(token_id),
        "timestamp": snapshot.get("timestamp"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": compute_mid(best_bid, best_ask),
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "hash": snapshot.get("hash"),
        "indexed_at": snapshot.get("indexed_at"),
        "market": snapshot.get("market"),
        "raw_bids": safe_json(bids),
        "raw_asks": safe_json(asks),
    }


def fetch_polymarket_orderbook(game_id, team, token_id, start_ms, end_ms):
    if not token_id or pd.isna(token_id):
        return []

    token_id = str(token_id)

    url = f"{BASE_URL}/polymarket/orderbooks"

    params = {
        "token_id": token_id,
        "start_time": start_ms,
        "end_time": end_ms,
        "limit": LIMIT,
    }

    print(f"Fetching Polymarket BBO: {game_id} | {team} | {token_id[:20]}...")

    snapshots = fetch_paginated(url, params, "snapshots")

    rows = [
        normalize_polymarket_snapshot(game_id, team, token_id, snapshot)
        for snapshot in snapshots
    ]

    print(f"  Polymarket snapshots for instrument: {len(rows)}")

    return rows


# ============================================================
# MAIN
# ============================================================

def main():
    ensure_dirs()
    reset_outputs_if_needed()

    full_market_map = pd.read_csv(MARKET_MAP_PATH)

    if END_GAME is not None:
        market_map = full_market_map.iloc[START_GAME - 1:END_GAME]
    else:
        market_map = full_market_map.iloc[START_GAME - 1:]

    completed_games = load_completed_games()

    total_kalshi_rows = 0
    total_poly_rows = 0

    for i, row in market_map.iterrows():
        game_id = row["game_id"]

        if game_id in completed_games:
            print(f"Skipping already completed game: {game_id}")
            continue

        matched_game = row.get("matched_game_clean", "")

        start_ms, end_ms = get_window_milliseconds(row)

        print("\n" + "=" * 100)
        print(f"Game {i + 1}/{len(full_market_map)}: {game_id} | {matched_game}")
        print(f"Window milliseconds: {start_ms} → {end_ms}")

        game_kalshi_rows = []
        game_poly_rows = []

        # ------------------------
        # Kalshi side 1
        # ------------------------
        game_kalshi_rows.extend(
            fetch_kalshi_orderbook(
                game_id=game_id,
                team=row.get("kalshi_team_1"),
                ticker=row.get("kalshi_ticker_1"),
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Kalshi side 2
        # ------------------------
        game_kalshi_rows.extend(
            fetch_kalshi_orderbook(
                game_id=game_id,
                team=row.get("kalshi_team_2"),
                ticker=row.get("kalshi_ticker_2"),
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Polymarket side 1
        # ------------------------
        game_poly_rows.extend(
            fetch_polymarket_orderbook(
                game_id=game_id,
                team=row.get("polymarket_team_a"),
                token_id=row.get("polymarket_token_id_1"),
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Polymarket side 2
        # ------------------------
        game_poly_rows.extend(
            fetch_polymarket_orderbook(
                game_id=game_id,
                team=row.get("polymarket_team_b"),
                token_id=row.get("polymarket_token_id_2"),
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

        # Save after each game
        append_rows_to_csv(game_kalshi_rows, KALSHI_OUT)
        append_rows_to_csv(game_poly_rows, POLY_OUT)

        mark_game_completed(game_id)

        total_kalshi_rows += len(game_kalshi_rows)
        total_poly_rows += len(game_poly_rows)

        print(f"Saved game {game_id}")
        print(f"  Kalshi rows this game: {len(game_kalshi_rows)}")
        print(f"  Polymarket rows this game: {len(game_poly_rows)}")
        print(f"  Kalshi total rows this run: {total_kalshi_rows}")
        print(f"  Polymarket total rows this run: {total_poly_rows}")

        time.sleep(REQUEST_SLEEP_SECONDS)

    print("\nDone.")
    print(f"Saved Kalshi BBO to: {KALSHI_OUT}")
    print(f"Saved Polymarket BBO to: {POLY_OUT}")


if __name__ == "__main__":
    main()