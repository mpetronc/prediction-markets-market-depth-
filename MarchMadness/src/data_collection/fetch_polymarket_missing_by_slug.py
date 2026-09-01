from pathlib import Path
import json
import requests
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUT_RAW_DIR = PROJECT_ROOT / "data/matched_markets/polymarket_singular_markets/missing_raw_events"
OUT_CSV = PROJECT_ROOT / "data/matched_markets/polymarket_singular_markets/polymarket_extra_candidates.csv"


MISSING_SLUGS = [
    {
        "slug": "cbb-ndkst-mst-2026-03-19",
        "team_a_clean": "North Dakota State",
        "team_b_clean": "Michigan State",
    },
    {
        "slug": "cbb-hawaii-ark-2026-03-19",
        "team_a_clean": "Hawaii",
        "team_b_clean": "Arkansas",
    },
    {
        "slug": "cbb-txam-stmry-2026-03-19",
        "team_a_clean": "Texas A&M",
        "team_b_clean": "Saint Mary's",
    },
    {
        "slug": "cbb-penn-ill-2026-03-19",
        "team_a_clean": "Penn",
        "team_b_clean": "Illinois",
    },
    {
        "slug": "cbb-idaho-hou-2026-03-19",
        "team_a_clean": "Idaho",
        "team_b_clean": "Houston",
    },
    {
        "slug": "cbb-queen-pur-2026-03-20",
        "team_a_clean": "Queens (N.C.)",
        "team_b_clean": "Purdue",
    },
    {
        "slug": "cbb-pvam-fl-2026-03-20",
        "team_a_clean": "Prairie View A&M",
        "team_b_clean": "Florida",
    },
    {
        "slug": "cbb-missr-mia-2026-03-20",
        "team_a_clean": "Missouri",
        "team_b_clean": "Miami (FL)",
    },
    {
        "slug": "cbb-mia-pur-2026-03-22",
        "team_a_clean": "Miami (FL)",
        "team_b_clean": "Purdue",
    },
]


def fetch_event_by_slug(slug):
    url = f"https://gamma-api.polymarket.com/events/slug/{slug}"
    response = requests.get(url, timeout=30)

    print("\nFETCH:", slug)
    print("status:", response.status_code)
    print("url:", response.url)

    if response.status_code != 200:
        print("FAILED:", response.text[:1000])
        return None

    return response.json()


def safe_json_load(value):
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []

    return []


def normalize_text(value):
    return str(value or "").strip().lower()


def is_main_winner_market(event, market):
    event_title = str(event.get("title", "")).strip()
    question = str(market.get("question", "")).strip()
    outcomes = safe_json_load(market.get("outcomes"))

    if not question:
        return False

    lower_question = normalize_text(question)

    if "spread:" in lower_question:
        return False

    if "o/u" in lower_question:
        return False

    if "over" in lower_question or "under" in lower_question:
        return False

    if ":" in question:
        return False

    if len(outcomes) != 2:
        return False

    outcome_text = " ".join(str(x).lower() for x in outcomes)

    if "over" in outcome_text or "under" in outcome_text:
        return False

    return question == event_title


def market_to_row(event, market, team_a_clean, team_b_clean):
    outcomes = safe_json_load(market.get("outcomes"))
    clob_token_ids = safe_json_load(market.get("clobTokenIds"))

    return {
        "source": "polymarket_gamma_slug",
        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "event_slug": event.get("slug"),
        "market_id": market.get("id"),
        "condition_id": market.get("conditionId"),
        "question_id": market.get("questionID"),
        "question": market.get("question"),
        "title": market.get("question") or event.get("title"),
        "slug": market.get("slug"),
        "outcomes": json.dumps(outcomes),
        "clob_token_ids": json.dumps(clob_token_ids),
        "closed": market.get("closed"),
        "active": market.get("active"),
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "start_date": event.get("startDate"),
        "end_date": event.get("endDate"),
        "team_a_clean": team_a_clean,
        "team_b_clean": team_b_clean,
        "game_clean": f"{team_a_clean} vs {team_b_clean}",
        "market_source_type": "game_winner",
    }


def main():
    print("Project root:", PROJECT_ROOT)

    OUT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    failed_slugs = []
    multi_match_slugs = []
    zero_match_slugs = []

    for item in MISSING_SLUGS:
        slug = item["slug"]
        team_a_clean = item["team_a_clean"]
        team_b_clean = item["team_b_clean"]

        event = fetch_event_by_slug(slug)

        if event is None:
            failed_slugs.append(slug)
            continue

        raw_path = OUT_RAW_DIR / f"{slug}.json"
        raw_path.write_text(json.dumps(event, indent=2), encoding="utf-8")
        print("saved raw:", raw_path)

        markets = event.get("markets", [])
        print("event title:", event.get("title"))
        print("markets found in event:", len(markets))

        main_markets = []

        for market in markets:
            if is_main_winner_market(event, market):
                main_markets.append(market)

        print("main winner markets found:", len(main_markets))

        if len(main_markets) == 0:
            zero_match_slugs.append(slug)
            print("WARNING: no main winner market found for", slug)

        if len(main_markets) > 1:
            multi_match_slugs.append(slug)
            print("WARNING: multiple main winner markets found for", slug)

        for market in main_markets:
            print("\nKEEP MARKET")
            print("market id:", market.get("id"))
            print("question:", market.get("question"))
            print("outcomes:", safe_json_load(market.get("outcomes")))
            print("conditionId:", market.get("conditionId"))
            print("clobTokenIds:", market.get("clobTokenIds"))

            rows.append(market_to_row(event, market, team_a_clean, team_b_clean))

    df = pd.DataFrame(rows)

    if len(df) > 0:
        df = df.drop_duplicates(subset=["condition_id"])
        df = df.sort_values(["event_slug", "market_id"])

    df.to_csv(OUT_CSV, index=False)

    print("\nSaved CSV:", OUT_CSV)
    print("Rows:", len(df))

    if len(df) > 0:
        visible_cols = [
            "event_slug",
            "title",
            "team_a_clean",
            "team_b_clean",
            "condition_id",
            "clob_token_ids",
            "closed",
            "active",
        ]
        print(df[visible_cols].to_string(index=False))

    print("\nSummary:")
    print("Requested slugs:", len(MISSING_SLUGS))
    print("Recovered winner markets:", len(df))
    print("Failed slugs:", failed_slugs)
    print("Zero main-market slugs:", zero_match_slugs)
    print("Multiple main-market slugs:", multi_match_slugs)


if __name__ == "__main__":
    main()