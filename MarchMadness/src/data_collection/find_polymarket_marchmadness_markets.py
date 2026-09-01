from pathlib import Path
import os
import time
import json
import re
import requests
import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("PREDX_API_KEY")
BASE_URL = "https://api.predexon.com/v2"

OUT_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_candidate_markets.csv"
MIXED_OUT_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_removed_mixed_games.csv"
NEITHER_OUT_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_removed_neither_games.csv"
RAW_DEBUG_PATH = PROJECT_ROOT / "data/matched_markets/polymarket_first_page_raw.json"

HEADERS = {"x-api-key": API_KEY}

START_DATE = "2026-03-17"
END_DATE = "2026-04-07"

LIMIT = 100
MAX_PAGES = 80


MARCH_MADNESS_TEAMS_2026 = {
    "UMBC", "Howard",
    "Prairie View A&M", "Lehigh",
    "Miami (OH)", "SMU",
    "Texas", "NC State",
    "Duke", "Siena",
    "TCU", "Ohio State",
    "Troy", "Nebraska",
    "High Point", "Wisconsin",
    "South Florida", "Louisville",
    "McNeese", "Vanderbilt",
    "Hawaii", "Arkansas",
    "North Dakota State", "Michigan State",
    "VCU", "North Carolina",
    "Michigan",
    "Texas A&M", "Saint Mary's",
    "BYU",
    "Penn", "Illinois",
    "Saint Louis", "Georgia",
    "Idaho", "Houston",
    "Kennesaw State", "Gonzaga",
    "Santa Clara", "Kentucky",
    "LIU", "Arizona",
    "Wright State", "Virginia",
    "Tennessee State", "Iowa State",
    "Hofstra", "Alabama",
    "Utah State", "Villanova",
    "Miami (FL)", "Missouri",
    "UCF", "UCLA",
    "Florida", "Clemson",
    "Iowa",
    "Northern Iowa", "St. John's",
    "Queens", "Purdue",
    "Cal Baptist", "Kansas",
    "Furman", "UConn",
    "Texas Tech", "Akron",
    "Tennessee",
}


TEAM_ALIASES = {
    "umbc retrievers": "UMBC",
    "umbc": "UMBC",
    "howard bison": "Howard",
    "howard": "Howard",

    "prairie view a&m panthers": "Prairie View A&M",
    "prairie view a&m": "Prairie View A&M",
    "prairie view": "Prairie View A&M",
    "lehigh mountain hawks": "Lehigh",
    "lehigh": "Lehigh",

    "miami (oh) redhawks": "Miami (OH)",
    "miami (oh)": "Miami (OH)",
    "miami ohio": "Miami (OH)",
    "smu mustangs": "SMU",
    "smu": "SMU",

    "texas longhorns": "Texas",
    "texas": "Texas",
    "north carolina state wolfpack": "NC State",
    "nc state": "NC State",
    "n.c. state": "NC State",

    "duke blue devils": "Duke",
    "duke": "Duke",
    "siena saints": "Siena",
    "siena": "Siena",

    "tcu horned frogs": "TCU",
    "tcu": "TCU",
    "ohio state buckeyes": "Ohio State",
    "ohio state": "Ohio State",
    "ohio st.": "Ohio State",

    "troy trojans": "Troy",
    "troy": "Troy",
    "nebraska cornhuskers": "Nebraska",
    "nebraska": "Nebraska",

    "high point panthers": "High Point",
    "high point": "High Point",
    "wisconsin badgers": "Wisconsin",
    "wisconsin": "Wisconsin",

    "south florida bulls": "South Florida",
    "south florida": "South Florida",
    "louisville cardinals": "Louisville",
    "louisville": "Louisville",

    "mcneese state cowboys": "McNeese",
    "mcneese cowboys": "McNeese",
    "mcneese state": "McNeese",
    "mcneese": "McNeese",
    "vanderbilt commodores": "Vanderbilt",
    "vanderbilt": "Vanderbilt",

    "hawai'i rainbow warriors": "Hawaii",
    "hawaii rainbow warriors": "Hawaii",
    "hawai'i": "Hawaii",
    "hawaii": "Hawaii",
    "arkansas razorbacks": "Arkansas",
    "arkansas": "Arkansas",

    "north dakota state bison": "North Dakota State",
    "north dakota state": "North Dakota State",
    "north dakota st.": "North Dakota State",
    "michigan state spartans": "Michigan State",
    "michigan state": "Michigan State",
    "michigan st.": "Michigan State",

    "vcu rams": "VCU",
    "vcu": "VCU",
    "north carolina tar heels": "North Carolina",
    "north carolina": "North Carolina",
    "michigan wolverines": "Michigan",
    "michigan": "Michigan",

    "texas a&m aggies": "Texas A&M",
    "texas a&m": "Texas A&M",
    "saint mary's gaels": "Saint Mary's",
    "saint mary's": "Saint Mary's",
    "st. mary's": "Saint Mary's",
    "byu cougars": "BYU",
    "byu": "BYU",

    "penn quakers": "Penn",
    "pennsylvania quakers": "Penn",
    "penn": "Penn",
    "illinois fighting illini": "Illinois",
    "illinois": "Illinois",

    "saint louis billikens": "Saint Louis",
    "saint louis": "Saint Louis",
    "st. louis": "Saint Louis",
    "georgia bulldogs": "Georgia",
    "georgia": "Georgia",

    "idaho vandals": "Idaho",
    "idaho": "Idaho",
    "houston cougars": "Houston",
    "houston": "Houston",

    "kennesaw state owls": "Kennesaw State",
    "kennesaw state": "Kennesaw State",
    "kennesaw st.": "Kennesaw State",
    "gonzaga bulldogs": "Gonzaga",
    "gonzaga": "Gonzaga",

    "santa clara broncos": "Santa Clara",
    "santa clara": "Santa Clara",
    "kentucky wildcats": "Kentucky",
    "kentucky": "Kentucky",

    "liu sharks": "LIU",
    "long island sharks": "LIU",
    "long island": "LIU",
    "liu": "LIU",
    "arizona wildcats": "Arizona",
    "arizona": "Arizona",

    "wright state raiders": "Wright State",
    "wright state": "Wright State",
    "wright st.": "Wright State",
    "virginia cavaliers": "Virginia",
    "virginia": "Virginia",

    "tennessee state tigers": "Tennessee State",
    "tennessee state": "Tennessee State",
    "tennessee st.": "Tennessee State",
    "iowa state cyclones": "Iowa State",
    "iowa state": "Iowa State",
    "iowa st.": "Iowa State",

    "hofstra pride": "Hofstra",
    "hofstra": "Hofstra",
    "alabama crimson tide": "Alabama",
    "alabama": "Alabama",

    "utah state aggies": "Utah State",
    "utah state": "Utah State",
    "utah st.": "Utah State",
    "villanova wildcats": "Villanova",
    "villanova": "Villanova",

    "miami (fl) hurricanes": "Miami (FL)",
    "miami (fl)": "Miami (FL)",
    "miami fl": "Miami (FL)",
    "missouri tigers": "Missouri",
    "missouri": "Missouri",

    "ucf knights": "UCF",
    "ucf": "UCF",
    "ucla bruins": "UCLA",
    "ucla": "UCLA",

    "florida gators": "Florida",
    "florida": "Florida",
    "clemson tigers": "Clemson",
    "clemson": "Clemson",
    "iowa hawkeyes": "Iowa",
    "iowa": "Iowa",

    "northern iowa panthers": "Northern Iowa",
    "northern iowa": "Northern Iowa",
    "st. john's red storm": "St. John's",
    "saint john's red storm": "St. John's",
    "st. john's": "St. John's",

    "queens (nc) royals": "Queens",
    "queens university": "Queens",
    "queens (n.c.)": "Queens",
    "queens nc": "Queens",
    "queens": "Queens",
    "purdue boilermakers": "Purdue",
    "purdue": "Purdue",

    "california baptist lancers": "Cal Baptist",
    "california baptist": "Cal Baptist",
    "cal baptist": "Cal Baptist",
    "kansas jayhawks": "Kansas",
    "kansas": "Kansas",

    "furman paladins": "Furman",
    "furman": "Furman",
    "connecticut huskies": "UConn",
    "uconn huskies": "UConn",
    "connecticut": "UConn",
    "uconn": "UConn",

    "texas tech red raiders": "Texas Tech",
    "texas tech": "Texas Tech",
    "akron zips": "Akron",
    "akron": "Akron",

    "tennessee volunteers": "Tennessee",
    "tennessee": "Tennessee",
}


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalized_text(value):
    text = clean_text(value).lower()
    text = text.replace("&amp;", "&")
    text = text.replace(" vs. ", " vs ")
    text = text.replace(" v. ", " vs ")
    text = text.replace("-", " ")
    text = " ".join(text.split())
    return text


def safe_json_loads(value):
    if value is None:
        return None

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return value

    try:
        return json.loads(value)
    except Exception:
        return None


def canonical_team_name(raw_name):
    text = normalized_text(raw_name)

    for alias in sorted(TEAM_ALIASES.keys(), key=len, reverse=True):
        if alias in text:
            return TEAM_ALIASES[alias]

    return clean_text(raw_name)


def get_text_field(market):
    return (
        market.get("title")
        or market.get("question")
        or market.get("event_title")
        or market.get("eventTitle")
        or ""
    )


def get_slug(market):
    return (
        market.get("slug")
        or market.get("market_slug")
        or market.get("marketSlug")
        or market.get("event_slug")
        or market.get("eventSlug")
        or ""
    )


def parse_teams_from_title(title):
    title = clean_text(title)

    if " vs. " in title:
        left, right = title.split(" vs. ", 1)
    elif " vs " in title:
        left, right = title.split(" vs ", 1)
    elif " at " in title:
        left, right = title.split(" at ", 1)
    else:
        return None, None

    team_a = canonical_team_name(left)
    team_b = canonical_team_name(right)

    return team_a, team_b


def parse_teams_from_outcomes(market):
    outcomes = safe_json_loads(market.get("outcomes"))

    if not isinstance(outcomes, list):
        return None, None

    if len(outcomes) != 2:
        return None, None

    labels = []

    for item in outcomes:
        if not isinstance(item, dict):
            return None, None

        label = item.get("label")
        labels.append(label)

    team_a = canonical_team_name(labels[0])
    team_b = canonical_team_name(labels[1])

    return team_a, team_b


def is_cbb_market(market):
    slug = normalized_text(get_slug(market))
    event_slug = normalized_text(market.get("event_slug") or market.get("eventSlug"))

    return slug.startswith("cbb") or event_slug.startswith("cbb")


def date_in_window(market):
    possible_dates = [
        market.get("end_date"),
        market.get("endDate"),
        market.get("start_date"),
        market.get("startDate"),
        market.get("end_time"),
        market.get("endTime"),
        market.get("start_time"),
        market.get("startTime"),
        market.get("event_date"),
        market.get("eventDate"),
    ]

    for value in possible_dates:
        if value is None or pd.isna(value):
            continue

        date_str = str(value)[:10]

        if START_DATE <= date_str <= END_DATE:
            return True

    slug = clean_text(get_slug(market))
    match = re.search(r"20\d{2}-\d{2}-\d{2}", slug)

    if match:
        date_str = match.group(0)
        return START_DATE <= date_str <= END_DATE

    return False


def is_plain_game_title(market):
    title = normalized_text(get_text_field(market))
    return " vs " in title or " at " in title


def is_not_derivative_market(market):
    title = normalized_text(market.get("title"))
    question = normalized_text(market.get("question"))
    event_title = normalized_text(market.get("event_title") or market.get("eventTitle"))
    slug = normalized_text(get_slug(market))

    combined = f"{title} {question} {event_title} {slug}"

    banned_terms = [
        "spread",
        "total",
        "over",
        "under",
        "win the 2026 ncaa tournament",
        "2026 ncaa tournament winner",
        "tournament winner",
        "championship",
        "final four",
        "conference",
    ]

    return not any(term in combined for term in banned_terms)


def has_exactly_two_team_outcomes(market):
    outcomes = safe_json_loads(market.get("outcomes"))

    if not isinstance(outcomes, list):
        return False

    if len(outcomes) != 2:
        return False

    labels = []

    for item in outcomes:
        if not isinstance(item, dict):
            return False

        label = normalized_text(item.get("label"))
        labels.append(label)

    bad_labels = {"yes", "no", "over", "under"}

    if any(label in bad_labels for label in labels):
        return False

    team_a = canonical_team_name(labels[0])
    team_b = canonical_team_name(labels[1])

    if team_a == team_b:
        return False

    if team_a not in MARCH_MADNESS_TEAMS_2026:
        return False

    if team_b not in MARCH_MADNESS_TEAMS_2026:
        return False

    return True


def classify_game(team_a, team_b):
    team_a_in = team_a in MARCH_MADNESS_TEAMS_2026
    team_b_in = team_b in MARCH_MADNESS_TEAMS_2026

    if team_a_in and team_b_in:
        return "both_mm"

    if team_a_in or team_b_in:
        return "mixed_one_mm_one_not"

    return "neither_mm"


def get_market_teams(market):
    outcome_team_a, outcome_team_b = parse_teams_from_outcomes(market)

    if outcome_team_a is not None and outcome_team_b is not None:
        return outcome_team_a, outcome_team_b

    title_team_a, title_team_b = parse_teams_from_title(get_text_field(market))
    return title_team_a, title_team_b


def get_reject_reason(market):
    if not is_cbb_market(market):
        return "not_cbb"

    if not date_in_window(market):
        return "outside_date_window"

    if not is_plain_game_title(market):
        return "not_plain_game_title"

    if not is_not_derivative_market(market):
        return "derivative_or_future_market"

    if not has_exactly_two_team_outcomes(market):
        return "bad_outcomes"

    team_a, team_b = get_market_teams(market)

    if team_a is None or team_b is None:
        return "could_not_parse_teams"

    if classify_game(team_a, team_b) != "both_mm":
        return "teams_not_both_mm"

    return "keep"


def is_valid_polymarket_marchmadness_market(market):
    return get_reject_reason(market) == "keep"


def flatten_market(market):
    team_a, team_b = get_market_teams(market)

    if team_a is None:
        team_a = ""

    if team_b is None:
        team_b = ""

    return {
        "market_id": market.get("market_id") or market.get("id"),
        "condition_id": market.get("condition_id"),
        "question_id": market.get("question_id"),
        "token_id": market.get("token_id"),
        "question": market.get("question"),
        "title": market.get("title"),
        "event_id": market.get("event_id"),
        "event_title": market.get("event_title") or market.get("eventTitle"),
        "event_slug": market.get("event_slug") or market.get("eventSlug"),
        "slug": get_slug(market),
        "status": market.get("status"),
        "outcomes": market.get("outcomes"),
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "best_bid": market.get("best_bid"),
        "best_ask": market.get("best_ask"),
        "last_trade_price": market.get("last_trade_price"),
        "start_date": market.get("start_date") or market.get("startDate"),
        "end_date": market.get("end_date") or market.get("endDate"),
        "team_a": team_a,
        "team_b": team_b,
        "game_classification": classify_game(team_a, team_b),
        "reject_reason": get_reject_reason(market),
    }


def fetch_polymarket_page(pagination_key=None):
    url = f"{BASE_URL}/polymarket/markets"

    params = {
        "limit": LIMIT,
    }

    if pagination_key:
        params["pagination_key"] = pagination_key

    response = requests.get(url, headers=HEADERS, params=params, timeout=30)

    print(f"Status: {response.status_code}")
    print(response.url)

    if response.status_code != 200:
        print(response.text[:1000])
        response.raise_for_status()

    return response.json()


def extract_markets(data):
    if isinstance(data, dict):
        for key in ["markets", "data", "results"]:
            if key in data and isinstance(data[key], list):
                return data[key]

    if isinstance(data, list):
        return data

    return []


def extract_pagination_key(data):
    if not isinstance(data, dict):
        return None

    pagination = data.get("pagination")

    if isinstance(pagination, dict):
        return pagination.get("pagination_key") or pagination.get("next_pagination_key")

    return data.get("pagination_key") or data.get("next_pagination_key")


def main():
    if not API_KEY:
        raise RuntimeError("Missing PREDX_API_KEY in .env")

    all_candidates = []
    mixed_removed = []
    neither_removed = []
    reject_reasons = {}

    pagination_key = None

    for page in range(1, MAX_PAGES + 1):
        print(f"\nFetching Polymarket page {page}...")

        data = fetch_polymarket_page(pagination_key=pagination_key)
        markets = extract_markets(data)

        print(f"Markets returned: {len(markets)}")

        if page == 1:
            RAW_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
            RAW_DEBUG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

        page_candidates = []

        for market in markets:
            row = flatten_market(market)
            reason = row["reject_reason"]
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

            if reason == "keep":
                page_candidates.append(row)
            elif row["game_classification"] == "mixed_one_mm_one_not":
                mixed_removed.append(row)
            elif row["game_classification"] == "neither_mm":
                neither_removed.append(row)

        print(f"Valid March Madness Polymarket candidates on page: {len(page_candidates)}")

        all_candidates.extend(page_candidates)

        pagination_key = extract_pagination_key(data)

        if not pagination_key:
            print("No more pagination key.")
            break

        time.sleep(0.2)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(all_candidates)

    if not df.empty:
        df = df.drop_duplicates(subset=["market_id", "condition_id", "slug"])
        df = df.sort_values(
            by=["end_date", "event_title", "title"],
            na_position="last",
        )

    df.to_csv(OUT_PATH, index=False)

    print(f"\nSaved: {OUT_PATH}")
    print(f"Total valid Polymarket candidates: {len(df)}")

    if not df.empty:
        print(f"Unique Polymarket events: {df['event_id'].nunique(dropna=True)}")

    print("\nReject / keep reason counts:")
    print(pd.Series(reject_reasons).sort_values(ascending=False))

    mixed_df = pd.DataFrame(mixed_removed)
    neither_df = pd.DataFrame(neither_removed)

    mixed_df.to_csv(MIXED_OUT_PATH, index=False)
    neither_df.to_csv(NEITHER_OUT_PATH, index=False)

    print(f"\nSaved mixed-game diagnostics: {MIXED_OUT_PATH}")
    print(f"Saved neither-MM diagnostics: {NEITHER_OUT_PATH}")
    print(f"Saved first raw page debug: {RAW_DEBUG_PATH}")

    if not df.empty:
        print("\nFirst 80 Polymarket candidate markets:")
        print(df.head(80).to_string(index=False))


if __name__ == "__main__":
    main()