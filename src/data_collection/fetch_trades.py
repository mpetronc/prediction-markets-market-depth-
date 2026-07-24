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

KALSHI_OUT = PROJECT_ROOT / "data/processed/kalshi/trades.csv"
POLY_OUT = PROJECT_ROOT / "data/processed/polymarket/trades.csv"

COMPLETED_PATH = PROJECT_ROOT / "data/processed/fetch_trades_completed_games.txt"

# Fetch all 67 by default
START_GAME = 1
END_GAME = 67

# For each game date, fetch from date - 5 days until date + 2 days.
# Example: Mar 20 game => Mar 15 to Mar 22.
LOOKBACK_DAYS = 5
AFTER_DAYS = 2

LIMIT = 500
ORDER = "asc"

REQUEST_SLEEP_SECONDS = 0.10
RETRY_SLEEP_SECONDS = 5

# Set to True for a fresh full run.
# Set to False if the script crashed and you want to resume.
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
    """
    First try Kalshi event ticker, e.g.
    KXNCAAMBGAME-26MAR20AKRTTU => 2026-03-20

    If championship/future style has no date, fall back to Polymarket slug:
    cbb-uconn-mich-2026-04-06 => 2026-04-06
    """

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

    match = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", kalshi_event_ticker)
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


def get_window_seconds(row):
    game_date = parse_game_date(row)

    start_dt = game_date - timedelta(days=LOOKBACK_DAYS)
    end_dt = game_date + timedelta(days=AFTER_DAYS)

    start_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())

    return start_s, end_s


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

        print(f"    got {len(items)} trades, total {len(all_items)}, has_more={has_more}")

        if not has_more or not pagination_key:
            break

        page += 1
        time.sleep(REQUEST_SLEEP_SECONDS)

    return all_items


def clean_float(value):
    if value is None or value == "":
        return None

    try:
        return float(value)
    except Exception:
        return None


def normalize_kalshi_price(value):
    """
    Predexon may return Kalshi prices either as 27 or 0.27.
    We normalize to decimal probability, e.g. 0.27.
    """
    value = clean_float(value)

    if value is None:
        return None

    if value > 1:
        return value / 100

    return value


def normalize_polymarket_price(value):
    return clean_float(value)


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# KALSHI
# ============================================================

def normalize_kalshi_trade(game_id, team, ticker, trade):
    yes_price = normalize_kalshi_price(trade.get("yes_price"))
    no_price = normalize_kalshi_price(trade.get("no_price"))

    side = trade.get("taker_side")

    size = trade.get("count")
    try:
        size = float(size)
    except Exception:
        size = None

    price = yes_price

    if side == "yes":
        signed_size = size
    elif side == "no":
        signed_size = -size if size is not None else None
    else:
        signed_size = None

    notional = None
    if price is not None and size is not None:
        notional = price * size

    return {
        "game_id": game_id,
        "platform": "kalshi",
        "team": team,
        "instrument_id": ticker,
        "trade_id": trade.get("trade_id"),
        "timestamp": trade.get("created_time"),
        "price": price,
        "yes_price": yes_price,
        "no_price": no_price,
        "size": size,
        "signed_size": signed_size,
        "notional": notional,
        "side": side,
        "raw_json": safe_json(trade),
    }


def fetch_kalshi_trades(game_id, team, ticker, start_s, end_s):
    if not ticker or pd.isna(ticker):
        return []

    url = f"{BASE_URL}/kalshi/trades"

    params = {
        "ticker": ticker,
        "start_time": start_s,
        "end_time": end_s,
        "limit": LIMIT,
        "order": ORDER,
    }

    print(f"Fetching Kalshi trades: {game_id} | {team} | {ticker}")

    trades = fetch_paginated(url, params, "trades")

    rows = [
        normalize_kalshi_trade(game_id, team, ticker, trade)
        for trade in trades
    ]

    print(f"  Kalshi trades for instrument: {len(rows)}")

    return rows


# ============================================================
# POLYMARKET
# ============================================================

def normalize_polymarket_trade(game_id, team, token_id, trade):
    price = normalize_polymarket_price(trade.get("price"))

    size = trade.get("shares_normalized")
    if size is None:
        size = trade.get("shares")

    try:
        size = float(size)
    except Exception:
        size = None

    side = trade.get("side")

    if side == "BUY":
        signed_size = size
    elif side == "SELL":
        signed_size = -size if size is not None else None
    else:
        signed_size = None

    amount_usd = clean_float(trade.get("amount_usd"))

    if amount_usd is not None:
        notional = amount_usd
    elif price is not None and size is not None:
        notional = price * size
    else:
        notional = None

    return {
        "game_id": game_id,
        "platform": "polymarket",
        "team": team,
        "instrument_id": token_id,
        "trade_id": trade.get("tx_hash"),
        "order_hash": trade.get("order_hash"),
        "timestamp": trade.get("timestamp"),
        "price": price,
        "size": size,
        "signed_size": signed_size,
        "notional": notional,
        "side": side,
        "condition_id": trade.get("condition_id"),
        "market_slug": trade.get("market_slug"),
        "outcome_label": trade.get("outcome_label"),
        "is_yes_side": trade.get("is_yes_side"),
        "fee_usd": clean_float(trade.get("fee_usd")),
        "raw_json": safe_json(trade),
    }


def fetch_polymarket_trades(game_id, team, token_id, start_s, end_s):
    if not token_id or pd.isna(token_id):
        return []

    token_id = str(token_id)

    url = f"{BASE_URL}/polymarket/trades"

    params = {
        "token_id": token_id,
        "start_time": start_s,
        "end_time": end_s,
        "limit": LIMIT,
        "order": ORDER,
    }

    print(f"Fetching Polymarket trades: {game_id} | {team} | {token_id[:20]}...")

    trades = fetch_paginated(url, params, "trades")

    rows = [
        normalize_polymarket_trade(game_id, team, token_id, trade)
        for trade in trades
    ]

    print(f"  Polymarket trades for instrument: {len(rows)}")

    return rows


# ============================================================
# MAIN
# ============================================================

def main():
    ensure_dirs()
    reset_outputs_if_needed()

    market_map = pd.read_csv(MARKET_MAP_PATH)

    if END_GAME is not None:
        market_map = market_map.iloc[START_GAME - 1:END_GAME]
    else:
        market_map = market_map.iloc[START_GAME - 1:]

    completed_games = load_completed_games()

    total_kalshi_rows = 0
    total_poly_rows = 0

    for i, row in market_map.iterrows():
        game_id = row["game_id"]

        if game_id in completed_games:
            print(f"Skipping already completed game: {game_id}")
            continue

        matched_game = row.get("matched_game_clean", "")

        start_s, end_s = get_window_seconds(row)

        print("\n" + "=" * 100)
        print(f"Game {i + 1}/{len(pd.read_csv(MARKET_MAP_PATH))}: {game_id} | {matched_game}")
        print(f"Window seconds: {start_s} → {end_s}")

        game_kalshi_rows = []
        game_poly_rows = []

        # ------------------------
        # Kalshi side 1
        # ------------------------
        game_kalshi_rows.extend(
            fetch_kalshi_trades(
                game_id=game_id,
                team=row.get("kalshi_team_1"),
                ticker=row.get("kalshi_ticker_1"),
                start_s=start_s,
                end_s=end_s,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Kalshi side 2
        # ------------------------
        game_kalshi_rows.extend(
            fetch_kalshi_trades(
                game_id=game_id,
                team=row.get("kalshi_team_2"),
                ticker=row.get("kalshi_ticker_2"),
                start_s=start_s,
                end_s=end_s,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Polymarket side 1
        # ------------------------
        game_poly_rows.extend(
            fetch_polymarket_trades(
                game_id=game_id,
                team=row.get("polymarket_team_a"),
                token_id=row.get("polymarket_token_id_1"),
                start_s=start_s,
                end_s=end_s,
            )
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

        # ------------------------
        # Polymarket side 2
        # ------------------------
        game_poly_rows.extend(
            fetch_polymarket_trades(
                game_id=game_id,
                team=row.get("polymarket_team_b"),
                token_id=row.get("polymarket_token_id_2"),
                start_s=start_s,
                end_s=end_s,
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
    print(f"Saved Kalshi trades to: {KALSHI_OUT}")
    print(f"Saved Polymarket trades to: {POLY_OUT}")


if __name__ == "__main__":
    main()
