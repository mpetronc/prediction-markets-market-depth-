from pathlib import Path
import json
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

NORMAL_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_singular_markets/polymarket_candidate_markets.csv"
EXTRA_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_singular_markets/polymarket_extra_candidates.csv"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_all_markets.csv"


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

        "Michigan St.": "Michigan State",
        "Michigan St": "Michigan State",
        "Michigan State": "Michigan State",
        "Michigan State Spartans": "Michigan State",

        "North Dakota St.": "North Dakota State",
        "North Dakota St": "North Dakota State",
        "North Dakota State": "North Dakota State",
        "North Dakota State Bison": "North Dakota State",

        "Ohio St.": "Ohio State",
        "Ohio St": "Ohio State",
        "Ohio State": "Ohio State",
        "Ohio State Buckeyes": "Ohio State",

        "Utah St.": "Utah State",
        "Utah St": "Utah State",
        "Utah State": "Utah State",
        "Utah State Aggies": "Utah State",

        "Tennessee St.": "Tennessee State",
        "Tennessee St": "Tennessee State",
        "Tennessee State": "Tennessee State",
        "Tennessee State Tigers": "Tennessee State",

        "Wright St.": "Wright State",
        "Wright St": "Wright State",
        "Wright State": "Wright State",
        "Wright State Raiders": "Wright State",

        "Kennesaw St.": "Kennesaw State",
        "Kennesaw St": "Kennesaw State",
        "Kennesaw State": "Kennesaw State",
        "Kennesaw State Owls": "Kennesaw State",

        "Iowa St.": "Iowa State",
        "Iowa St": "Iowa State",
        "Iowa State": "Iowa State",
        "Iowa State Cyclones": "Iowa State",

        "NC St.": "NC State",
        "NC St": "NC State",
        "NC State": "NC State",
        "North Carolina State": "NC State",
        "North Carolina State Wolfpack": "NC State",

        "St. John's": "Saint John's",
        "St John's": "Saint John's",
        "Saint John's": "Saint John's",
        "St. John's Red Storm": "Saint John's",
        "Saint John's Red Storm": "Saint John's",

        "Saint Mary's": "Saint Mary's",
        "St. Mary's": "Saint Mary's",
        "St Mary's": "Saint Mary's",
        "Saint Mary's Gaels": "Saint Mary's",

        "Cal Baptist": "California Baptist",
        "California Baptist": "California Baptist",
        "California Baptist Lancers": "California Baptist",

        "LIU": "Long Island",
        "Long Island": "Long Island",
        "Long Island Sharks": "Long Island",

        "Queens University": "Queens (N.C.)",
        "Queens (N.C.)": "Queens (N.C.)",
        "Queens (NC)": "Queens (N.C.)",
        "Queens NC": "Queens (N.C.)",
        "Queens Royals": "Queens (N.C.)",
        "Queens (NC) Royals": "Queens (N.C.)",

        "Hawai'i": "Hawaii",
        "Hawaii": "Hawaii",
        "Hawaii Rainbow Warriors": "Hawaii",

        "UConn": "UConn",
        "Connecticut": "UConn",
        "Connecticut Huskies": "UConn",

        "Arkansas Razorbacks": "Arkansas",
        "Texas A&M Aggies": "Texas A&M",
        "Penn Quakers": "Penn",
        "Illinois Fighting Illini": "Illinois",
        "Idaho Vandals": "Idaho",
        "Houston Cougars": "Houston",
        "Prairie View A&M Panthers": "Prairie View A&M",
        "Florida Gators": "Florida",
        "Missouri Tigers": "Missouri",
        "Purdue Boilermakers": "Purdue",
    }

    return aliases.get(raw, raw)


def pair_key(team_a, team_b):
    a = clean_team_name(team_a)
    b = clean_team_name(team_b)
    return " || ".join(sorted([a, b]))


def safe_json_load(value):
    if pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []

    return []


def normalize_normal_candidates(df):
    df = df.copy()

    df["source"] = df.get("source", "predexon_polymarket_candidates")
    df["platform"] = "polymarket"
    df["market_source_type"] = df.get("market_source_type", "game_winner")

    if "team_a" not in df.columns:
        df["team_a"] = ""

    if "team_b" not in df.columns:
        df["team_b"] = ""

    df["team_a"] = df["team_a"].fillna("").astype(str).str.strip()
    df["team_b"] = df["team_b"].fillna("").astype(str).str.strip()

    df["team_a_clean"] = df["team_a"].apply(clean_team_name)
    df["team_b_clean"] = df["team_b"].apply(clean_team_name)

    df["game_clean"] = df["team_a_clean"] + " vs " + df["team_b_clean"]
    df["pair_key"] = df.apply(lambda row: pair_key(row["team_a_clean"], row["team_b_clean"]), axis=1)

    if "condition_id" not in df.columns and "conditionId" in df.columns:
        df["condition_id"] = df["conditionId"]

    if "condition_id" not in df.columns:
        df["condition_id"] = ""

    if "clob_token_ids" not in df.columns:
        df["clob_token_ids"] = ""

    if "event_slug" not in df.columns:
        df["event_slug"] = ""

    if "title" not in df.columns:
        if "question" in df.columns:
            df["title"] = df["question"]
        else:
            df["title"] = ""

    return df


def normalize_extra_candidates(df):
    df = df.copy()

    df["source"] = df.get("source", "polymarket_gamma_slug")
    df["platform"] = "polymarket"
    df["market_source_type"] = df.get("market_source_type", "game_winner")

    if "team_a_clean" not in df.columns:
        df["team_a_clean"] = ""

    if "team_b_clean" not in df.columns:
        df["team_b_clean"] = ""

    df["team_a_clean"] = df["team_a_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)
    df["team_b_clean"] = df["team_b_clean"].fillna("").astype(str).str.strip().apply(clean_team_name)

    if "team_a" not in df.columns:
        df["team_a"] = df["team_a_clean"]

    if "team_b" not in df.columns:
        df["team_b"] = df["team_b_clean"]

    df["game_clean"] = df["team_a_clean"] + " vs " + df["team_b_clean"]
    df["pair_key"] = df.apply(lambda row: pair_key(row["team_a_clean"], row["team_b_clean"]), axis=1)

    if "condition_id" not in df.columns and "conditionId" in df.columns:
        df["condition_id"] = df["conditionId"]

    if "condition_id" not in df.columns:
        df["condition_id"] = ""

    if "clob_token_ids" not in df.columns:
        df["clob_token_ids"] = ""

    if "event_slug" not in df.columns:
        df["event_slug"] = ""

    if "title" not in df.columns:
        if "question" in df.columns:
            df["title"] = df["question"]
        else:
            df["title"] = ""

    return df


def main():
    print("Project root:", PROJECT_ROOT)
    print("Normal Polymarket path:", NORMAL_PATH)
    print("Extra Polymarket path:", EXTRA_PATH)

    if not NORMAL_PATH.exists():
        raise FileNotFoundError(f"Missing normal Polymarket file: {NORMAL_PATH}")

    if not EXTRA_PATH.exists():
        raise FileNotFoundError(f"Missing extra Polymarket file: {EXTRA_PATH}")

    normal_df = pd.read_csv(NORMAL_PATH)
    extra_df = pd.read_csv(EXTRA_PATH)

    normal_df = normalize_normal_candidates(normal_df)
    extra_df = normalize_extra_candidates(extra_df)

    all_columns = sorted(set(normal_df.columns) | set(extra_df.columns))

    normal_df = normal_df.reindex(columns=all_columns)
    extra_df = extra_df.reindex(columns=all_columns)

    all_df = pd.concat([normal_df, extra_df], ignore_index=True)

    if "condition_id" in all_df.columns:
        all_df["condition_id"] = all_df["condition_id"].fillna("").astype(str)
        before = len(all_df)
        all_df = all_df.drop_duplicates(subset=["condition_id"])
        after = len(all_df)
        print("Dropped duplicate condition_id rows:", before - after)

    sort_cols = []
    for col in ["event_slug", "market_id", "condition_id"]:
        if col in all_df.columns:
            sort_cols.append(col)

    if sort_cols:
        all_df = all_df.sort_values(sort_cols, na_position="last")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(OUT_PATH, index=False)

    print("\nSaved:", OUT_PATH)
    print("Total Polymarket rows:", len(all_df))

    if "condition_id" in all_df.columns:
        print("Unique condition IDs:", all_df["condition_id"].nunique())

    print("Unique games by pair_key:", all_df["pair_key"].nunique())

    print("\nSource counts:")
    print(all_df["source"].value_counts(dropna=False))

    print("\nmarket_source_type counts:")
    print(all_df["market_source_type"].value_counts(dropna=False))

    print("\nEmpty clean-field checks:")
    print("Empty team_a_clean:", (all_df["team_a_clean"].fillna("").astype(str).str.strip() == "").sum())
    print("Empty team_b_clean:", (all_df["team_b_clean"].fillna("").astype(str).str.strip() == "").sum())
    print("Empty pair_key:", (all_df["pair_key"].fillna("").astype(str).str.strip() == "").sum())

    print("\nLast 15 rows:")
    visible_cols = [
        "event_slug",
        "title",
        "team_a_clean",
        "team_b_clean",
        "game_clean",
        "pair_key",
        "condition_id",
        "source",
    ]
    visible_cols = [c for c in visible_cols if c in all_df.columns]
    print(all_df[visible_cols].tail(15).to_string(index=False))


if __name__ == "__main__":
    main()