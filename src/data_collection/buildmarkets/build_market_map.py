from pathlib import Path
import json
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

MATCHED_PATH = PROJECT_ROOT / "data/matched_markets/MM_matched_markets.csv"
OUT_PATH = PROJECT_ROOT / "data/matched_markets/MM_market_map.csv"


def safe_str(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def safe_json_load(value):
    if pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return []
        try:
            return json.loads(value)
        except Exception:
            return []

    return []


def make_game_id(index):
    return f"MM_GAME_{index:03d}"


def main():
    print("Project root:", PROJECT_ROOT)
    print("Matched markets path:", MATCHED_PATH)

    if not MATCHED_PATH.exists():
        raise FileNotFoundError(f"Missing matched markets file: {MATCHED_PATH}")

    matched_df = pd.read_csv(MATCHED_PATH)

    rows = []

    for i, (_, row) in enumerate(matched_df.iterrows(), start=1):
        clob_token_ids = safe_json_load(row.get("polymarket_clob_token_ids", ""))

        polymarket_token_id_1 = safe_str(row.get("polymarket_token_id_1", ""))
        polymarket_token_id_2 = safe_str(row.get("polymarket_token_id_2", ""))

        if polymarket_token_id_1 == "" and len(clob_token_ids) > 0:
            polymarket_token_id_1 = str(clob_token_ids[0])

        if polymarket_token_id_2 == "" and len(clob_token_ids) > 1:
            polymarket_token_id_2 = str(clob_token_ids[1])

        market_map_row = {
            "game_id": make_game_id(i),

            "matched_game_clean": safe_str(row.get("matched_game_clean", "")),
            "pair_key": safe_str(row.get("pair_key", "")),
            "match_method": safe_str(row.get("match_method", "")),

            "kalshi_event_ticker": safe_str(row.get("kalshi_event_ticker", "")),
            "kalshi_market_source_type": safe_str(row.get("kalshi_market_source_type", "")),

            "kalshi_ticker_1": safe_str(row.get("kalshi_ticker_1", "")),
            "kalshi_team_1": safe_str(row.get("kalshi_team_1", "")),
            "kalshi_result_1": safe_str(row.get("kalshi_result_1", "")),
            "kalshi_title_1": safe_str(row.get("kalshi_title_1", "")),

            "kalshi_ticker_2": safe_str(row.get("kalshi_ticker_2", "")),
            "kalshi_team_2": safe_str(row.get("kalshi_team_2", "")),
            "kalshi_result_2": safe_str(row.get("kalshi_result_2", "")),
            "kalshi_title_2": safe_str(row.get("kalshi_title_2", "")),

            "polymarket_event_slug": safe_str(row.get("polymarket_event_slug", "")),
            "polymarket_event_title": safe_str(row.get("polymarket_event_title", "")),
            "polymarket_title": safe_str(row.get("polymarket_title", "")),
            "polymarket_market_id": safe_str(row.get("polymarket_market_id", "")),
            "polymarket_condition_id": safe_str(row.get("polymarket_condition_id", "")),
            "polymarket_question_id": safe_str(row.get("polymarket_question_id", "")),

            "polymarket_team_a": safe_str(row.get("polymarket_team_a", "")),
            "polymarket_team_b": safe_str(row.get("polymarket_team_b", "")),

            "polymarket_clob_token_ids": json.dumps(clob_token_ids),
            "polymarket_token_id_1": polymarket_token_id_1,
            "polymarket_token_id_2": polymarket_token_id_2,

            "polymarket_source": safe_str(row.get("polymarket_source", "")),
            "polymarket_market_source_type": safe_str(row.get("polymarket_market_source_type", "")),
        }

        rows.append(market_map_row)

    map_df = pd.DataFrame(rows)

    if len(map_df) > 0:
        map_df = map_df.sort_values("game_id")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    map_df.to_csv(OUT_PATH, index=False)

    print("\nSaved:", OUT_PATH)
    print("Rows:", len(map_df))
    print("Unique game IDs:", map_df["game_id"].nunique())
    print("Unique Kalshi event tickers:", map_df["kalshi_event_ticker"].nunique())
    print("Unique Polymarket condition IDs:", map_df["polymarket_condition_id"].nunique())

    print("\nEmpty ID checks:")
    print("Empty kalshi_event_ticker:", (map_df["kalshi_event_ticker"] == "").sum())
    print("Empty kalshi_ticker_1:", (map_df["kalshi_ticker_1"] == "").sum())
    print("Empty kalshi_ticker_2:", (map_df["kalshi_ticker_2"] == "").sum())
    print("Empty polymarket_condition_id:", (map_df["polymarket_condition_id"] == "").sum())
    print("Empty polymarket_token_id_1:", (map_df["polymarket_token_id_1"] == "").sum())
    print("Empty polymarket_token_id_2:", (map_df["polymarket_token_id_2"] == "").sum())

    print("\nPreview:")
    preview_cols = [
        "game_id",
        "matched_game_clean",
        "kalshi_event_ticker",
        "kalshi_ticker_1",
        "kalshi_ticker_2",
        "polymarket_event_slug",
        "polymarket_condition_id",
        "polymarket_token_id_1",
        "polymarket_token_id_2",
    ]
    preview_cols = [c for c in preview_cols if c in map_df.columns]
    print(map_df[preview_cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()