from pathlib import Path
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

KALSHI_PATH = PROJECT_ROOT / "data/matched_markets/kalshi_all_markets.csv"
POLY_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_singular_markets/polymarket_candidate_markets.csv"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/missing_polymarket_games.csv"


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
        "Queens NC": "Queens (N.C.)",
        "Queens Royals": "Queens (N.C.)",

        "Hawai'i": "Hawaii",
        "Hawaii": "Hawaii",
        "Hawaii Rainbow Warriors": "Hawaii",

        "UConn": "UConn",
        "Connecticut": "UConn",
        "Connecticut Huskies": "UConn",
    }

    return aliases.get(raw, raw)


def pair_key(a, b):
    a_clean = clean_team_name(a)
    b_clean = clean_team_name(b)
    return " || ".join(sorted([a_clean, b_clean]))


def main():
    if not KALSHI_PATH.exists():
        raise FileNotFoundError(f"Missing Kalshi file: {KALSHI_PATH}")

    if not POLY_PATH.exists():
        raise FileNotFoundError(f"Missing Polymarket file: {POLY_PATH}")

    kalshi_df = pd.read_csv(KALSHI_PATH)
    poly_df = pd.read_csv(POLY_PATH)

    kalshi_games = (
        kalshi_df
        .drop_duplicates(subset=["event_ticker"])
        .copy()
    )

    kalshi_games["kalshi_pair_key"] = kalshi_games.apply(
        lambda row: pair_key(row.get("away_team_clean", row.get("away_team", "")),
                             row.get("home_team_clean", row.get("home_team", ""))),
        axis=1,
    )

    if "team_a" in poly_df.columns and "team_b" in poly_df.columns:
        poly_df["poly_pair_key"] = poly_df.apply(
            lambda row: pair_key(row.get("team_a", ""), row.get("team_b", "")),
            axis=1,
        )
    elif "team_a_clean" in poly_df.columns and "team_b_clean" in poly_df.columns:
        poly_df["poly_pair_key"] = poly_df.apply(
            lambda row: pair_key(row.get("team_a_clean", ""), row.get("team_b_clean", "")),
            axis=1,
        )
    else:
        raise ValueError("Polymarket file needs team_a/team_b or team_a_clean/team_b_clean columns.")

    poly_keys = set(poly_df["poly_pair_key"].dropna())

    missing = kalshi_games[~kalshi_games["kalshi_pair_key"].isin(poly_keys)].copy()

    out_cols = [
        "event_ticker",
        "game_clean",
        "away_team_clean",
        "home_team_clean",
        "market_source_type",
        "close_time",
        "kalshi_pair_key",
    ]
    out_cols = [c for c in out_cols if c in missing.columns]

    missing[out_cols].to_csv(OUT_PATH, index=False)

    print("Kalshi unique games:", len(kalshi_games))
    print("Polymarket unique candidate games:", poly_df["poly_pair_key"].nunique())
    print("Missing from Polymarket candidate file:", len(missing))
    print("Saved:", OUT_PATH)

    print("\nMissing games:")
    print(missing[out_cols].to_string(index=False))


if __name__ == "__main__":
    main()