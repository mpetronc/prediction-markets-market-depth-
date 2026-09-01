from __future__ import annotations

import argparse
import csv
import json
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/summary"
)
TOURNAMENT_NAME = "NCAA Men's Basketball Championship"
USER_AGENT = "march-madness-research/1.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "game_cutoffs.csv"


def build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


SSL_CONTEXT = build_ssl_context()

OUTPUT_COLUMNS = [
    "espn_event_id",
    "game",
    "away_team",
    "home_team",
    "away_score",
    "home_score",
    "round",
    "espn_headline",
    "status",
    "scheduled_start_utc",
    "first_play_utc",
    "end_timestamp_utc",
    "end_timestamp_unix_s",
    "end_timestamp_unix_ms",
    "end_timestamp_source",
    "espn_game_url",
]


def fetch_json(
    base_url: str,
    params: dict[str, Any],
    *,
    timeout: float,
    max_attempts: int = 4,
) -> dict[str, Any]:
    url = f"{base_url}?{urlencode(params)}"
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )

    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
                return json.load(response)
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == max_attempts:
                raise RuntimeError(f"ESPN returned HTTP {exc.code} for {url}") from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                install_command = (
                    f'"{sys.executable}" -m pip install --upgrade certifi'
                )
                raise RuntimeError(
                    "Python could not verify ESPN's SSL certificate. Run this "
                    f"once in the PyCharm terminal, then rerun the script:\n"
                    f"  {install_command}"
                ) from exc
            if attempt == max_attempts:
                raise RuntimeError(f"Could not fetch {url}: {exc}") from exc

        time.sleep(2 ** (attempt - 1))

    raise AssertionError("retry loop ended unexpectedly")


def tournament_headline(competition: dict[str, Any]) -> str:
    for note in competition.get("notes", []):
        headline = str(note.get("headline", ""))
        if TOURNAMENT_NAME.casefold() in headline.casefold():
            return headline
    return ""


def get_competitor(
    competitors: list[dict[str, Any]], home_away: str
) -> dict[str, Any]:
    for competitor in competitors:
        if competitor.get("homeAway") == home_away:
            return competitor
    return {}


def normalized_utc(timestamp: str | None) -> tuple[str, str, str]:
    if not timestamp:
        return "", "", ""

    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp, "", ""

    unix_ms = int(round(parsed.timestamp() * 1000))
    iso_utc = (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    return iso_utc, str(unix_ms // 1000), str(unix_ms)


def play_wallclock(play: dict[str, Any]) -> str:
    return str(play.get("wallclock") or "")


def find_game_timestamps(
    summary: dict[str, Any],
) -> tuple[str, str, str]:
    plays = summary.get("plays", [])
    timestamped_plays = [play for play in plays if play_wallclock(play)]
    first_play = play_wallclock(timestamped_plays[0]) if timestamped_plays else ""

    end_game_plays = []
    for play in timestamped_plays:
        play_type = play.get("type", {})
        type_id = str(play_type.get("id", ""))
        type_text = str(play_type.get("text", "")).casefold()
        play_text = str(play.get("text", "")).casefold()
        if (
            type_id == "402"
            or type_text in {"end game", "endgame"}
            or play_text == "end of game"
        ):
            end_game_plays.append(play)

    if end_game_plays:
        return first_play, play_wallclock(end_game_plays[-1]), "espn_end_game_play"

    completed = (
        summary.get("header", {})
        .get("competitions", [{}])[0]
        .get("status", {})
        .get("type", {})
        .get("completed", False)
    )
    if completed and timestamped_plays:
        return first_play, play_wallclock(timestamped_plays[-1]), "last_timestamped_play_fallback"

    return first_play, "", "unavailable"


def fetch_tournament_events(year: int, timeout: float) -> list[dict[str, Any]]:
    date_range = f"{year}0315-{year}0410"
    scoreboard = fetch_json(
        SCOREBOARD_URL,
        {"dates": date_range, "limit": 1000, "groups": 100},
        timeout=timeout,
    )

    events = []
    for event in scoreboard.get("events", []):
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        competition = competitions[0]
        if tournament_headline(competition):
            events.append(event)

    events.sort(key=lambda event: (event.get("date", ""), event.get("id", "")))
    return events


def event_to_row(
    event: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, str]:
    competition = event["competitions"][0]
    competitors = competition.get("competitors", [])
    away = get_competitor(competitors, "away")
    home = get_competitor(competitors, "home")
    away_team = str(away.get("team", {}).get("displayName", ""))
    home_team = str(home.get("team", {}).get("displayName", ""))

    event_id = str(event["id"])
    summary = fetch_json(
        SUMMARY_URL,
        {"event": event_id},
        timeout=timeout,
    )
    first_play, end_timestamp, end_source = find_game_timestamps(summary)
    end_utc, end_unix_s, end_unix_ms = normalized_utc(end_timestamp)
    first_play_utc, _, _ = normalized_utc(first_play)
    scheduled_start_utc, _, _ = normalized_utc(event.get("date"))

    headline = tournament_headline(competition)
    tournament_round = headline.rsplit(" - ", 1)[-1] if headline else ""
    status = competition.get("status", {}).get("type", {}).get("detail", "")

    return {
        "espn_event_id": event_id,
        "game": f"{away_team} vs. {home_team}",
        "away_team": away_team,
        "home_team": home_team,
        "away_score": str(away.get("score", "")),
        "home_score": str(home.get("score", "")),
        "round": tournament_round,
        "espn_headline": headline,
        "status": str(status),
        "scheduled_start_utc": scheduled_start_utc,
        "first_play_utc": first_play_utc,
        "end_timestamp_utc": end_utc,
        "end_timestamp_unix_s": end_unix_s,
        "end_timestamp_unix_ms": end_unix_ms,
        "end_timestamp_source": end_source,
        "espn_game_url": (
            "https://www.espn.com/mens-college-basketball/game/_/gameId/"
            f"{event_id}"
        ),
    }


def failed_event_row(event: dict[str, Any]) -> dict[str, str]:
    competition = event["competitions"][0]
    competitors = competition.get("competitors", [])
    away = get_competitor(competitors, "away")
    home = get_competitor(competitors, "home")
    away_team = str(away.get("team", {}).get("displayName", ""))
    home_team = str(home.get("team", {}).get("displayName", ""))
    scheduled_start_utc, _, _ = normalized_utc(event.get("date"))
    headline = tournament_headline(competition)
    return {
        "espn_event_id": str(event.get("id", "")),
        "game": f"{away_team} vs. {home_team}",
        "away_team": away_team,
        "home_team": home_team,
        "away_score": str(away.get("score", "")),
        "home_score": str(home.get("score", "")),
        "round": headline.rsplit(" - ", 1)[-1] if headline else "",
        "espn_headline": headline,
        "status": "summary_request_failed",
        "scheduled_start_utc": scheduled_start_utc,
        "first_play_utc": "",
        "end_timestamp_utc": "",
        "end_timestamp_unix_s": "",
        "end_timestamp_unix_ms": "",
        "end_timestamp_source": "request_failed",
        "espn_game_url": (
            "https://www.espn.com/mens-college-basketball/game/_/gameId/"
            f"{event.get('id', '')}"
        ),
    }


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch ESPN's recorded End of Game timestamp for every men's "
            "March Madness game."
        )
    )
    parser.add_argument("--year", type=int, default=2026, help="Tournament year")
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output CSV path (default: <project root>/data/game_cutoffs.csv)"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Concurrent ESPN summary requests (default: 6)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        print("ERROR: --workers must be at least 1.", file=sys.stderr)
        return 1
    output_path = args.output or DEFAULT_OUTPUT

    try:
        events = fetch_tournament_events(args.year, args.timeout)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not events:
        print(
            f"ERROR: ESPN returned no March Madness games for {args.year}.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(events)} March Madness games for {args.year}.")
    if args.year >= 2011 and args.year != 2020 and len(events) != 67:
        print(
            f"WARNING: expected 67 games but found {len(events)}; inspect the output.",
            file=sys.stderr,
        )

    rows_by_index: dict[int, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_event = {
            executor.submit(event_to_row, event, timeout=args.timeout): (index, event)
            for index, event in enumerate(events)
        }
        completed_count = 0
        for future in as_completed(future_to_event):
            index, event = future_to_event[future]
            completed_count += 1
            label = event.get("shortName", event.get("name", ""))
            try:
                rows_by_index[index] = future.result()
                print(f"[{completed_count:02d}/{len(events)}] {label}")
            except RuntimeError as exc:
                print(f"[{completed_count:02d}/{len(events)}] {label} -- FAILED")
                print(f"  WARNING: {exc}", file=sys.stderr)
                rows_by_index[index] = failed_event_row(event)

    rows = [rows_by_index[index] for index in range(len(events))]

    write_csv(rows, output_path)

    missing = sum(not row["end_timestamp_utc"] for row in rows)
    fallback = sum(
        row["end_timestamp_source"] == "last_timestamped_play_fallback"
        for row in rows
    )
    print(f"Saved {len(rows)} games to {output_path.resolve()}")
    print(f"Missing end timestamps: {missing}; fallback timestamps: {fallback}")
    return 0 if missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())