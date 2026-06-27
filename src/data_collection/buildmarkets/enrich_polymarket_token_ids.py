from pathlib import Path
import json
import time
import requests
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

POLYMARKET_ALL_PATH = PROJECT_ROOT / "data/matched_markets/polymarket/polymarket_all_markets.csv"
BACKUP_PATH = PROJECT_ROOT / "data/matched_markets/polymarket/polymarket_all_markets_before_token_enrichment.csv"

RAW_DIR = PROJECT_ROOT / "data/matched_markets/polymarket/polymarket_singular_markets/token_enrichment_raw_events"


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


def normalize_text(value):
    return str(value or "").strip().lower()


def fetch_event_by_slug(slug):
    url = f"https://gamma-api.polymarket.com/events/slug/{slug}"

    response = requests.get(url, timeout=30)

    print("\nFETCH:", slug)
    print("status:", response.status_code)

    if response.status_code != 200:
        print("FAILED:", response.text[:500])
        return None

    return response.json()


def is_main_winner_market(event, market):
    event_title = safe_str(event.get("title", ""))
    question = safe_str(market.get("question", ""))
    outcomes = safe_json_load(market.get("outcomes"))

    if question == "":
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


def find_main_winner_market(event):
    markets = event.get("markets", [])
    main_markets = []

    for market in markets:
        if is_main_winner_market(event, market):
            main_markets.append(market)

    return main_markets


def main():
    print("Project root:", PROJECT_ROOT)
    print("Polymarket all-markets path:", POLYMARKET_ALL_PATH)

    if not POLYMARKET_ALL_PATH.exists():
        raise FileNotFoundError(f"Missing file: {POLYMARKET_ALL_PATH}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(POLYMARKET_ALL_PATH)

    if "event_slug" not in df.columns:
        raise ValueError("polymarket_all_markets.csv is missing event_slug column")

    if "clob_token_ids" not in df.columns:
        df["clob_token_ids"] = ""

    if "polymarket_token_id_1" not in df.columns:
        df["polymarket_token_id_1"] = ""

    if "polymarket_token_id_2" not in df.columns:
        df["polymarket_token_id_2"] = ""

    df.to_csv(BACKUP_PATH, index=False)
    print("Backup saved:", BACKUP_PATH)

    success = 0
    failed = []
    zero_main = []
    multi_main = []

    for idx, row in df.iterrows():
        slug = safe_str(row.get("event_slug", ""))

        if slug == "":
            failed.append("(empty slug)")
            continue

        existing_tokens = safe_json_load(row.get("clob_token_ids", ""))

        if len(existing_tokens) == 2:
            df.at[idx, "polymarket_token_id_1"] = str(existing_tokens[0])
            df.at[idx, "polymarket_token_id_2"] = str(existing_tokens[1])
            success += 1
            print("\nSKIP already has tokens:", slug)
            continue

        event = fetch_event_by_slug(slug)

        if event is None:
            failed.append(slug)
            continue

        raw_path = RAW_DIR / f"{slug}.json"
        raw_path.write_text(json.dumps(event, indent=2), encoding="utf-8")

        main_markets = find_main_winner_market(event)

        print("event title:", event.get("title"))
        print("markets found:", len(event.get("markets", [])))
        print("main winner markets found:", len(main_markets))

        if len(main_markets) == 0:
            zero_main.append(slug)
            continue

        if len(main_markets) > 1:
            multi_main.append(slug)

        main_market = main_markets[0]

        clob_token_ids = safe_json_load(main_market.get("clobTokenIds"))

        if len(clob_token_ids) != 2:
            print("WARNING: bad clobTokenIds:", clob_token_ids)
            failed.append(slug)
            continue

        print("KEEP:")
        print("market id:", main_market.get("id"))
        print("question:", main_market.get("question"))
        print("clobTokenIds:", clob_token_ids)

        df.at[idx, "clob_token_ids"] = json.dumps(clob_token_ids)
        df.at[idx, "polymarket_token_id_1"] = str(clob_token_ids[0])
        df.at[idx, "polymarket_token_id_2"] = str(clob_token_ids[1])

        success += 1

        time.sleep(0.1)

    df.to_csv(POLYMARKET_ALL_PATH, index=False)

    print("\nSaved enriched file:", POLYMARKET_ALL_PATH)
    print("Rows:", len(df))
    print("Successful token rows:", success)

    print("\nEmpty token checks:")
    print(
        "Empty clob_token_ids:",
        (df["clob_token_ids"].fillna("").astype(str).str.strip() == "").sum(),
    )
    print(
        "Empty polymarket_token_id_1:",
        (df["polymarket_token_id_1"].fillna("").astype(str).str.strip() == "").sum(),
    )
    print(
        "Empty polymarket_token_id_2:",
        (df["polymarket_token_id_2"].fillna("").astype(str).str.strip() == "").sum(),
    )

    print("\nProblems:")
    print("Failed:", failed)
    print("Zero main winner market:", zero_main)
    print("Multiple main winner markets:", multi_main)

    preview_cols = [
        "event_slug",
        "title",
        "condition_id",
        "clob_token_ids",
        "polymarket_token_id_1",
        "polymarket_token_id_2",
    ]
    preview_cols = [c for c in preview_cols if c in df.columns]

    print("\nPreview:")
    print(df[preview_cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()