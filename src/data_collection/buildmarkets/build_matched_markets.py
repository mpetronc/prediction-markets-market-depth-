from pathlib import Path
import json
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

KALSHI_PATH = PROJECT_ROOT / "data/matched_markets/kalshi/kalshi_all_markets.csv"
POLYMARKET_PATH = PROJECT_ROOT / "data/matched_markets/polymarket/polymarket_all_markets.csv"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/MM_matched_markets.csv"

UNMATCHED_KALSHI_PATH = PROJECT_ROOT / "data/matched_markets/kalshi/unmatched_kalshi_markets.csv"
UNMATCHED_POLYMARKET_PATH = PROJECT_ROOT / "data/matched_markets/polymarket/unmatched_polymarket_markets.csv"


def clean_team_name(name):
    if pd.isna(name):
        return ""

    raw = str(name).strip()

    aliases = {
        "Miami (OH)": "Miami (Ohio)",
        "Miami OH": "Miami (Ohio)",
        "Miami Ohio": "Miami (Ohio)",
        "Miami (Ohio)": "Miami (Ohio)",
        "Miami RedHawks": "Miami (Ohio)",

        "Miami (FL)": "Miami (FL)",
        "Miami FL": "Miami (FL)",
        "Miami Hurricanes": "Miami (FL)",

        "Connecticut": "UConn",
        "Connecticut Huskies": "UConn",
        "UConn": "UConn",

        "Michigan Wolverines": "Michigan",
        "Michigan": "Michigan",

        "Michigan State Spartans": "Michigan State",
        "Michigan State": "Michigan State",
        "Michigan St.": "Michigan State",
        "Michigan St": "Michigan State",

        "North Dakota State Bison": "North Dakota State",
        "North Dakota State": "North Dakota State",
        "North Dakota St.": "North Dakota State",
        "North Dakota St": "North Dakota State",

        "Saint Mary's Gaels": "Saint Mary's",
        "Saint Mary's": "Saint Mary's",
        "St. Mary's": "Saint Mary's",
        "St Mary's": "Saint Mary's",

        "Queens": "Queens (N.C.)",
        "Queens Royals": "Queens (N.C.)",
        "Queens (NC) Royals": "Queens (N.C.)",
        "Queens (NC)": "Queens (N.C.)",
        "Queens (N.C.)": "Queens (N.C.)",
        "Queens NC": "Queens (N.C.)",
        "Queens University": "Queens (N.C.)",

        "Hawaii Rainbow Warriors": "Hawaii",
        "Hawai'i": "Hawaii",
        "Hawaii": "Hawaii",

        "NC State": "NC State",
        "North Carolina State": "NC State",
        "North Carolina State Wolfpack": "NC State",

        "Texas A&M Aggies": "Texas A&M",
        "Texas A&M": "Texas A&M",

        "Prairie View A&M Panthers": "Prairie View A&M",
        "Prairie View A&M": "Prairie View A&M",

        "Penn Quakers": "Penn",
        "Illinois Fighting Illini": "Illinois",
        "Idaho Vandals": "Idaho",
        "Houston Cougars": "Houston",
        "Arkansas Razorbacks": "Arkansas",
        "Florida Gators": "Florida",
        "Missouri Tigers": "Missouri",
        "Purdue Boilermakers": "Purdue",
    }

    return aliases.get(raw, raw)


def make_pair_key(team_1, team_2):
    a = clean_team_name(team_1)
    b = clean_team_name(team_2)
    return " || ".join(sorted([a, b]))


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


def prepare_kalshi(kalshi_df):
    df = kalshi_df.copy()

    for col in ["team_clean", "opponent_clean", "away_team_clean", "home_team_clean"]:
        if col not in df.columns:
            df[col] = ""

    df["team_clean"] = df["team_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)
    df["opponent_clean"] = df["opponent_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)

    df["pair_key"] = df.apply(
        lambda row: make_pair_key(row["team_clean"], row["opponent_clean"]),
        axis=1,
    )

    return df


def prepare_polymarket(poly_df):
    df = poly_df.copy()

    for col in ["team_a_clean", "team_b_clean"]:
        if col not in df.columns:
            df[col] = ""

    df["team_a_clean"] = df["team_a_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)
    df["team_b_clean"] = df["team_b_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)

    df["pair_key"] = df.apply(
        lambda row: make_pair_key(row["team_a_clean"], row["team_b_clean"]),
        axis=1,
    )

    return df


def summarize_kalshi_group(pair_key, group):
    group = group.copy()

    event_tickers = (
        sorted(group["event_ticker"].dropna().astype(str).unique())
        if "event_ticker" in group.columns
        else []
    )

    market_source_types = (
        sorted(group["market_source_type"].dropna().astype(str).unique())
        if "market_source_type" in group.columns
        else []
    )

    teams = sorted(group["team_clean"].dropna().astype(str).unique())

    row = {
        "pair_key": pair_key,
        "kalshi_event_ticker": event_tickers[0] if event_tickers else "",
        "kalshi_event_tickers_all": json.dumps(event_tickers),
        "kalshi_market_source_type": market_source_types[0] if market_source_types else "",
        "kalshi_market_source_types_all": json.dumps(market_source_types),
        "kalshi_num_rows": len(group),
        "kalshi_teams_all": json.dumps(teams),
    }

    sorted_group = group.sort_values("team_clean")

    for i, (_, r) in enumerate(sorted_group.iterrows(), start=1):
        row[f"kalshi_team_{i}"] = safe_str(r.get("team_clean", ""))
        row[f"kalshi_opponent_{i}"] = safe_str(r.get("opponent_clean", ""))
        row[f"kalshi_ticker_{i}"] = safe_str(r.get("ticker", ""))
        row[f"kalshi_title_{i}"] = safe_str(r.get("title", ""))
        row[f"kalshi_result_{i}"] = safe_str(r.get("result", ""))

    return row


def summarize_polymarket_row(row):
    outcomes = safe_json_load(row.get("outcomes", ""))
    clob_token_ids = safe_json_load(row.get("clob_token_ids", ""))

    return {
        "pair_key": safe_str(row.get("pair_key", "")),

        "polymarket_event_slug": safe_str(row.get("event_slug", "")),
        "polymarket_event_title": safe_str(row.get("event_title", "")),
        "polymarket_title": safe_str(row.get("title", "")),

        "polymarket_market_id": safe_str(row.get("market_id", "")),
        "polymarket_condition_id": safe_str(row.get("condition_id", "")),
        "polymarket_question_id": safe_str(row.get("question_id", "")),

        "polymarket_team_a": safe_str(row.get("team_a_clean", "")),
        "polymarket_team_b": safe_str(row.get("team_b_clean", "")),

        "polymarket_outcomes": json.dumps(outcomes),
        "polymarket_clob_token_ids": json.dumps(clob_token_ids),

        "polymarket_token_id_1": safe_str(row.get("polymarket_token_id_1", "")),
        "polymarket_token_id_2": safe_str(row.get("polymarket_token_id_2", "")),

        "polymarket_source": safe_str(row.get("source", "")),
        "polymarket_market_source_type": safe_str(row.get("market_source_type", "")),
        "polymarket_closed": safe_str(row.get("closed", "")),
        "polymarket_active": safe_str(row.get("active", "")),
    }


def main():
    print("Project root:", PROJECT_ROOT)
    print("Kalshi path:", KALSHI_PATH)
    print("Polymarket path:", POLYMARKET_PATH)

    if not KALSHI_PATH.exists():
        raise FileNotFoundError(f"Missing Kalshi file: {KALSHI_PATH}")

    if not POLYMARKET_PATH.exists():
        raise FileNotFoundError(f"Missing Polymarket file: {POLYMARKET_PATH}")

    kalshi_df = pd.read_csv(KALSHI_PATH)
    poly_df = pd.read_csv(POLYMARKET_PATH)

    kalshi_df = prepare_kalshi(kalshi_df)
    poly_df = prepare_polymarket(poly_df)

    kalshi_keys = set(kalshi_df["pair_key"].dropna().astype(str))
    poly_keys = set(poly_df["pair_key"].dropna().astype(str))

    matched_keys = sorted(kalshi_keys & poly_keys)
    unmatched_kalshi_keys = sorted(kalshi_keys - poly_keys)
    unmatched_poly_keys = sorted(poly_keys - kalshi_keys)

    print("\nInput summary:")
    print("Kalshi rows:", len(kalshi_df))
    print("Kalshi unique games:", len(kalshi_keys))
    print("Polymarket rows:", len(poly_df))
    print("Polymarket unique games:", len(poly_keys))

    print("\nMatch summary:")
    print("Matched games:", len(matched_keys))
    print("Unmatched Kalshi games:", len(unmatched_kalshi_keys))
    print("Unmatched Polymarket games:", len(unmatched_poly_keys))

    matched_rows = []

    kalshi_grouped = kalshi_df.groupby("pair_key", dropna=False)

    poly_by_key = {}
    duplicate_poly_keys = []

    for _, row in poly_df.iterrows():
        key = safe_str(row.get("pair_key", ""))

        if key in poly_by_key:
            duplicate_poly_keys.append(key)

        poly_by_key[key] = row

    for key in matched_keys:
        kalshi_summary = summarize_kalshi_group(key, kalshi_grouped.get_group(key))
        polymarket_summary = summarize_polymarket_row(poly_by_key[key])

        matched_row = {}
        matched_row.update(kalshi_summary)
        matched_row.update(polymarket_summary)

        matched_row["matched_game_clean"] = key.replace(" || ", " vs ")
        matched_row["match_method"] = "exact_pair_key"

        matched_rows.append(matched_row)

    matched_df = pd.DataFrame(matched_rows)

    if len(matched_df) > 0:
        matched_df = matched_df.sort_values("matched_game_clean")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    matched_df.to_csv(OUT_PATH, index=False)

    unmatched_kalshi_df = kalshi_df[kalshi_df["pair_key"].isin(unmatched_kalshi_keys)].copy()
    unmatched_poly_df = poly_df[poly_df["pair_key"].isin(unmatched_poly_keys)].copy()

    unmatched_kalshi_df.to_csv(UNMATCHED_KALSHI_PATH, index=False)
    unmatched_poly_df.to_csv(UNMATCHED_POLYMARKET_PATH, index=False)

    print("\nSaved matched file:", OUT_PATH)
    print("Saved unmatched Kalshi file:", UNMATCHED_KALSHI_PATH)
    print("Saved unmatched Polymarket file:", UNMATCHED_POLYMARKET_PATH)

    print("\nOutput rows:", len(matched_df))

    print("\nDuplicate Polymarket pair_keys:", sorted(set(duplicate_poly_keys)))

    if len(matched_df) > 0:
        visible_cols = [
            "matched_game_clean",
            "kalshi_event_ticker",
            "kalshi_ticker_1",
            "kalshi_ticker_2",
            "polymarket_event_slug",
            "polymarket_condition_id",
            "polymarket_token_id_1",
            "polymarket_token_id_2",
            "match_method",
        ]
        visible_cols = [c for c in visible_cols if c in matched_df.columns]

        print("\nPreview:")
        print(matched_df[visible_cols].head(20).to_string(index=False))

    if unmatched_kalshi_keys:
        print("\nUnmatched Kalshi keys:")
        for key in unmatched_kalshi_keys:
            print("-", key)

    if unmatched_poly_keys:
        print("\nUnmatched Polymarket keys:")
        for key in unmatched_poly_keys:
            print("-", key)


if __name__ == "__main__":
    main()