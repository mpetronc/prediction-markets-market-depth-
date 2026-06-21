from pathlib import Path
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

NORMAL_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_singular_markets/kalshi_candidate_markets.csv"
CHAMP_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_singular_markets/kalshi_championship_candidates.csv"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_all_markets.csv"


def add_missing_column(df, column_name, value):
    if column_name not in df.columns:
        df[column_name] = value
    else:
        df[column_name] = df[column_name].fillna(value)
    return df


def clean_team_name(name):
    if pd.isna(name):
        return ""

    raw = str(name).strip()

    aliases = {
        "Miami (OH)": "Miami (Ohio)",
        "Miami OH": "Miami (Ohio)",
        "Miami Ohio": "Miami (Ohio)",
        "Miami (Ohio)": "Miami (Ohio)",

        "Miami (FL)": "Miami (FL)",
        "Miami FL": "Miami (FL)",

        "Michigan St.": "Michigan State",
        "Michigan St": "Michigan State",
        "Michigan State": "Michigan State",

        "North Dakota St.": "North Dakota State",
        "North Dakota St": "North Dakota State",
        "North Dakota State": "North Dakota State",

        "Ohio St.": "Ohio State",
        "Ohio St": "Ohio State",
        "Ohio State": "Ohio State",

        "Utah St.": "Utah State",
        "Utah St": "Utah State",
        "Utah State": "Utah State",

        "Tennessee St.": "Tennessee State",
        "Tennessee St": "Tennessee State",
        "Tennessee State": "Tennessee State",

        "Wright St.": "Wright State",
        "Wright St": "Wright State",
        "Wright State": "Wright State",

        "Kennesaw St.": "Kennesaw State",
        "Kennesaw St": "Kennesaw State",
        "Kennesaw State": "Kennesaw State",

        "Iowa St.": "Iowa State",
        "Iowa St": "Iowa State",
        "Iowa State": "Iowa State",

        "NC St.": "NC State",
        "NC St": "NC State",
        "NC State": "NC State",
        "North Carolina State": "NC State",

        "St. John's": "Saint John's",
        "St John's": "Saint John's",
        "Saint John's": "Saint John's",

        "Saint Mary's": "Saint Mary's",
        "St. Mary's": "Saint Mary's",
        "St Mary's": "Saint Mary's",

        "Cal Baptist": "California Baptist",
        "California Baptist": "California Baptist",

        "LIU": "Long Island",
        "Long Island": "Long Island",

        "Queens University": "Queens (N.C.)",
        "Queens (N.C.)": "Queens (N.C.)",
        "Queens NC": "Queens (N.C.)",

        "Hawai'i": "Hawaii",
        "Hawaii": "Hawaii",

        "UConn": "UConn",
        "Connecticut": "UConn",
    }

    return aliases.get(raw, raw)


def infer_opponent_raw(row):
    team = str(row.get("team", "")).strip()
    away = str(row.get("away_team", "")).strip()
    home = str(row.get("home_team", "")).strip()

    team_clean = clean_team_name(team)
    away_clean = clean_team_name(away)
    home_clean = clean_team_name(home)

    if team_clean == away_clean:
        return home

    if team_clean == home_clean:
        return away

    return ""


def infer_opponent_clean(row):
    team_clean = str(row.get("team_clean", "")).strip()
    away_clean = str(row.get("away_team_clean", "")).strip()
    home_clean = str(row.get("home_team_clean", "")).strip()

    if team_clean == away_clean:
        return home_clean

    if team_clean == home_clean:
        return away_clean

    return ""


def make_game_name_raw(row):
    away = str(row.get("away_team", "")).strip()
    home = str(row.get("home_team", "")).strip()
    event_title = str(row.get("event_title", "")).strip()

    if away and home:
        return f"{away} vs {home}"

    if event_title:
        return event_title

    return ""


def make_game_name_clean(row):
    away = str(row.get("away_team_clean", "")).strip()
    home = str(row.get("home_team_clean", "")).strip()

    if away and home:
        return f"{away} vs {home}"

    return ""


def main():
    print("Project root:", PROJECT_ROOT)
    print("Normal Kalshi path:", NORMAL_PATH)
    print("Championship Kalshi path:", CHAMP_PATH)

    if not NORMAL_PATH.exists():
        raise FileNotFoundError(f"Missing normal Kalshi file: {NORMAL_PATH}")

    if not CHAMP_PATH.exists():
        raise FileNotFoundError(f"Missing Kalshi championship file: {CHAMP_PATH}")

    normal_df = pd.read_csv(NORMAL_PATH)
    champ_df = pd.read_csv(CHAMP_PATH)

    normal_df["platform"] = "kalshi"
    champ_df["platform"] = "kalshi"

    normal_df = add_missing_column(normal_df, "market_source_type", "game_winner")
    champ_df["market_source_type"] = "championship_future_equivalent"

    if "team" not in normal_df.columns:
        if "yes_subtitle" in normal_df.columns:
            normal_df["team"] = normal_df["yes_subtitle"]
        else:
            normal_df["team"] = ""

    if "away_team" not in normal_df.columns:
        normal_df["away_team"] = ""

    if "home_team" not in normal_df.columns:
        normal_df["home_team"] = ""

    normal_df["team"] = normal_df["team"].astype(str).str.strip()
    normal_df["away_team"] = normal_df["away_team"].astype(str).str.strip()
    normal_df["home_team"] = normal_df["home_team"].astype(str).str.strip()

    normal_df["team_clean"] = normal_df["team"].apply(clean_team_name)
    normal_df["away_team_clean"] = normal_df["away_team"].apply(clean_team_name)
    normal_df["home_team_clean"] = normal_df["home_team"].apply(clean_team_name)

    normal_df["opponent"] = normal_df.apply(infer_opponent_raw, axis=1)
    normal_df["opponent_clean"] = normal_df.apply(infer_opponent_clean, axis=1)

    normal_df["game"] = normal_df.apply(make_game_name_raw, axis=1)
    normal_df["game_clean"] = normal_df.apply(make_game_name_clean, axis=1)

    if "round" not in normal_df.columns:
        normal_df["round"] = ""

    if "game_classification" not in normal_df.columns:
        normal_df["game_classification"] = "both_mm"

    if "team" not in champ_df.columns:
        champ_df["team"] = ""

    if "opponent" not in champ_df.columns:
        champ_df["opponent"] = ""

    if "game" not in champ_df.columns:
        champ_df["game"] = "Michigan vs UConn"

    if "away_team" not in champ_df.columns:
        champ_df["away_team"] = "Michigan"

    if "home_team" not in champ_df.columns:
        champ_df["home_team"] = "UConn"

    if "round" not in champ_df.columns:
        champ_df["round"] = "National Championship"

    if "game_classification" not in champ_df.columns:
        champ_df["game_classification"] = "both_mm"

    champ_df["team"] = champ_df["team"].fillna("").astype(str).str.strip()
    champ_df["opponent"] = champ_df["opponent"].fillna("").astype(str).str.strip()
    champ_df["game"] = champ_df["game"].fillna("Michigan vs UConn")
    champ_df["away_team"] = champ_df["away_team"].fillna("Michigan")
    champ_df["home_team"] = champ_df["home_team"].fillna("UConn")
    champ_df["round"] = champ_df["round"].fillna("National Championship")
    champ_df["game_classification"] = champ_df["game_classification"].fillna("both_mm")

    champ_df["team_clean"] = champ_df["team"].apply(clean_team_name)
    champ_df["opponent_clean"] = champ_df["opponent"].apply(clean_team_name)
    champ_df["away_team_clean"] = champ_df["away_team"].apply(clean_team_name)
    champ_df["home_team_clean"] = champ_df["home_team"].apply(clean_team_name)
    champ_df["game_clean"] = champ_df.apply(make_game_name_clean, axis=1)

    all_columns = sorted(set(normal_df.columns) | set(champ_df.columns))

    normal_df = normal_df.reindex(columns=all_columns)
    champ_df = champ_df.reindex(columns=all_columns)

    all_df = pd.concat([normal_df, champ_df], ignore_index=True)

    if "ticker" in all_df.columns:
        all_df = all_df.drop_duplicates(subset=["ticker"])

    sort_cols = []
    for col in ["close_time", "event_ticker", "ticker"]:
        if col in all_df.columns:
            sort_cols.append(col)

    if sort_cols:
        all_df = all_df.sort_values(by=sort_cols, na_position="last")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(OUT_PATH, index=False)

    print("\nSaved:", OUT_PATH)
    print("Total Kalshi rows:", len(all_df))

    if "ticker" in all_df.columns:
        print("Unique tickers:", all_df["ticker"].nunique())

    if "event_ticker" in all_df.columns:
        print("Unique event tickers:", all_df["event_ticker"].nunique())

    print("\nmarket_source_type counts:")
    print(all_df["market_source_type"].value_counts(dropna=False))

    print("\nOpponent check:")
    empty_opponents = (all_df["opponent_clean"].fillna("").astype(str).str.strip() == "").sum()
    print("Empty opponent_clean:", empty_opponents)

    print("\nRows with empty opponent_clean:")
    empty_df = all_df[all_df["opponent_clean"].fillna("").astype(str).str.strip() == ""]
    if len(empty_df) == 0:
        print("None")
    else:
        visible_cols_empty = [
            "ticker",
            "event_ticker",
            "title",
            "team",
            "away_team",
            "home_team",
            "team_clean",
            "away_team_clean",
            "home_team_clean",
            "opponent_clean",
        ]
        visible_cols_empty = [col for col in visible_cols_empty if col in empty_df.columns]
        print(empty_df[visible_cols_empty].to_string(index=False))

    print("\nLast 10 rows:")
    visible_cols = [
        "ticker",
        "event_ticker",
        "title",
        "team",
        "opponent",
        "team_clean",
        "opponent_clean",
        "away_team_clean",
        "home_team_clean",
        "game_clean",
        "market_source_type",
        "close_time",
        "result",
    ]
    visible_cols = [col for col in visible_cols if col in all_df.columns]

    print(all_df[visible_cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()