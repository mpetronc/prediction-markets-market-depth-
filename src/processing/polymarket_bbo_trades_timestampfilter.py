from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def normalize_team(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\bsaint\b", "st", text)
    text = re.sub(r"\bst[.]\b", "st", text)
    text = re.sub(r"\blong island university\b", "liu", text)
    text = re.sub(r"\bconnecticut\b", "uconn", text)
    text = re.sub(r"\bpennsylvania\b", "penn", text)
    text = re.sub(r"\bcalifornia baptist\b", "cal baptist", text)
    text = re.sub(r"\bmiami fl\b", "miami", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def split_game(value: object) -> tuple[str, str] | None:
    text = str(value).strip()
    parts = re.split(r"\s+(?:vs[.]?|at)\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    return normalize_team(parts[0]), normalize_team(parts[1])


def pair_key(pair: tuple[str, str]) -> tuple[str, str]:
    return tuple(sorted(pair))


def team_similarity(left: str, right: str) -> float:
    sequence_score = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return sequence_score
    overlap = 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))
    return max(sequence_score, overlap)


def pair_similarity(left: tuple[str, str], right: tuple[str, str]) -> float:
    direct = (
        team_similarity(left[0], right[0]) + team_similarity(left[1], right[1])
    ) / 2
    reversed_score = (
        team_similarity(left[0], right[1]) + team_similarity(left[1], right[0])
    ) / 2
    return max(direct, reversed_score)


def map_games(market_map: pd.DataFrame, cutoffs: pd.DataFrame) -> pd.DataFrame:
    required_map = {"game_id", "polymarket_event_title", "matched_game_clean"}
    required_cutoffs = {
        "game",
        "first_play_utc",
        "end_timestamp_utc",
        "end_timestamp_unix_s",
        "end_timestamp_unix_ms",
    }
    missing_map = required_map - set(market_map.columns)
    missing_cutoffs = required_cutoffs - set(cutoffs.columns)
    if missing_map:
        raise ValueError(f"MM_market_map.csv is missing columns: {sorted(missing_map)}")
    if missing_cutoffs:
        raise ValueError(f"game_cutoffs.csv is missing columns: {sorted(missing_cutoffs)}")

    market_map = market_map.drop_duplicates("game_id").copy()
    cutoffs = cutoffs.copy().reset_index(drop=True)
    cutoff_pairs: dict[int, tuple[str, str]] = {}
    exact_cutoffs: dict[tuple[str, str], list[int]] = {}

    for cutoff_index, cutoff_row in cutoffs.iterrows():
        cutoff_pair = split_game(cutoff_row["game"])
        if cutoff_pair is None:
            raise ValueError(f"Could not split ESPN game name: {cutoff_row['game']}")
        cutoff_pairs[cutoff_index] = cutoff_pair
        exact_cutoffs.setdefault(pair_key(cutoff_pair), []).append(cutoff_index)

    title_columns = [
        column
        for column in [
            "polymarket_event_title",
            "polymarket_title",
            "matched_game_clean",
        ]
        if column in market_map.columns
    ]
    map_pairs: dict[int, list[tuple[str, str]]] = {}
    for map_index, map_row in market_map.iterrows():
        pairs = []
        for column in title_columns:
            value = map_row.get(column)
            if pd.isna(value):
                continue
            parsed = split_game(value)
            if parsed is not None and parsed not in pairs:
                pairs.append(parsed)
        if not pairs:
            raise ValueError(f"Could not split a game title for {map_row['game_id']}")
        map_pairs[map_index] = pairs

    assignments: dict[int, tuple[int, str, float]] = {}
    used_cutoffs: set[int] = set()

    for map_index, pairs in map_pairs.items():
        exact_matches = {
            cutoff_index
            for pair in pairs
            for cutoff_index in exact_cutoffs.get(pair_key(pair), [])
            if cutoff_index not in used_cutoffs
        }
        if len(exact_matches) == 1:
            cutoff_index = exact_matches.pop()
            assignments[map_index] = (cutoff_index, "exact_title", 1.0)
            used_cutoffs.add(cutoff_index)

    remaining_maps = set(map_pairs) - set(assignments)
    remaining_cutoffs = set(cutoff_pairs) - used_cutoffs

    while remaining_maps:
        candidates = []
        for map_index in remaining_maps:
            for cutoff_index in remaining_cutoffs:
                score = max(
                    pair_similarity(map_pair, cutoff_pairs[cutoff_index])
                    for map_pair in map_pairs[map_index]
                )
                candidates.append((score, map_index, cutoff_index))
        if not candidates:
            break
        score, map_index, cutoff_index = max(candidates)
        if score < 0.72:
            break
        assignments[map_index] = (cutoff_index, "fuzzy_title", score)
        remaining_maps.remove(map_index)
        remaining_cutoffs.remove(cutoff_index)

    if remaining_maps:
        failed_games = market_map.loc[sorted(remaining_maps), "game_id"].tolist()
        raise ValueError(
            "Could not safely match these game IDs to ESPN cutoffs: "
            f"{failed_games}"
        )

    if len(assignments) != len(market_map):
        raise ValueError(
            f"Matched {len(assignments)} of {len(market_map)} market-map games"
        )

    records = []
    for map_index, map_row in market_map.iterrows():
        cutoff_index, match_method, match_score = assignments[map_index]
        cutoff_row = cutoffs.loc[cutoff_index]
        first_play = pd.to_datetime(
            cutoff_row["first_play_utc"], utc=True, errors="coerce"
        )
        end_time = pd.to_datetime(
            cutoff_row["end_timestamp_utc"], utc=True, errors="coerce"
        )
        if pd.isna(first_play) or pd.isna(end_time):
            raise ValueError(f"Missing game window for {cutoff_row['game']}")
        records.append(
            {
                "game_id": str(map_row["game_id"]),
                "market_game": str(map_row["polymarket_event_title"]),
                "espn_game": str(cutoff_row["game"]),
                "start_ns": int(first_play.value),
                "end_ns": int(end_time.value),
                "match_method": match_method,
                "match_score": match_score,
            }
        )

    windows = pd.DataFrame(records)
    if windows["game_id"].duplicated().any():
        raise ValueError("Duplicate game_id values in the cutoff mapping")
    if windows["espn_game"].duplicated().any():
        duplicates = windows.loc[windows["espn_game"].duplicated(False), "espn_game"].tolist()
        raise ValueError(f"Multiple game IDs mapped to the same ESPN game: {duplicates}")
    return windows


def timestamps_to_ns(values: pd.Series) -> pd.Series:
    raw = values.astype("string").str.strip()
    numeric = pd.to_numeric(raw, errors="coerce")
    numeric_int = numeric.round().astype("Int64")
    absolute = numeric_int.abs()
    result = pd.Series(pd.NA, index=values.index, dtype="Int64")

    seconds = numeric_int.notna() & absolute.lt(100_000_000_000)
    milliseconds = (
        numeric_int.notna()
        & absolute.ge(100_000_000_000)
        & absolute.lt(100_000_000_000_000)
    )
    microseconds = (
        numeric_int.notna()
        & absolute.ge(100_000_000_000_000)
        & absolute.lt(100_000_000_000_000_000)
    )
    nanoseconds = numeric_int.notna() & absolute.ge(100_000_000_000_000_000)

    result.loc[seconds] = numeric_int.loc[seconds] * 1_000_000_000
    result.loc[milliseconds] = numeric_int.loc[milliseconds] * 1_000_000
    result.loc[microseconds] = numeric_int.loc[microseconds] * 1_000
    result.loc[nanoseconds] = numeric_int.loc[nanoseconds]

    datetime_values = numeric_int.isna() & raw.notna() & raw.ne("")
    if datetime_values.any():
        parsed = pd.to_datetime(raw.loc[datetime_values], utc=True, errors="coerce")
        parsed = parsed.loc[parsed.notna()]
        if not parsed.empty:
            result.loc[parsed.index] = parsed.astype("int64").astype("Int64")

    return result


def filter_file(
    input_path: Path,
    output_path: Path,
    windows: pd.DataFrame,
    chunksize: int,
) -> tuple[int, int, int, int, int]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    header = pd.read_csv(input_path, nrows=0)
    required = {"game_id", "timestamp"}
    missing = required - set(header.columns)
    if missing:
        raise ValueError(f"{input_path} is missing columns: {sorted(missing)}")

    start_by_game = windows.set_index("game_id")["start_ns"]
    end_by_game = windows.set_index("game_id")["end_ns"]
    known_games = set(start_by_game.index)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    total_rows = 0
    kept_rows = 0
    invalid_timestamp_rows = 0
    before_start_rows = 0
    after_end_rows = 0
    missing_game_id_rows = 0
    unknown_games: set[str] = set()
    wrote_header = False

    try:
        for chunk in pd.read_csv(
            input_path,
            chunksize=chunksize,
            low_memory=False,
            dtype={"game_id": "string"},
        ):
            original_columns = chunk.columns.tolist()
            game_ids = chunk["game_id"].astype("string")
            timestamps = timestamps_to_ns(chunk["timestamp"])
            unknown_games.update(set(game_ids.dropna()) - known_games)
            missing_game_id_rows += int(game_ids.isna().sum())
            starts = game_ids.map(start_by_game).astype("Int64")
            ends = game_ids.map(end_by_game)
            valid_timestamps = timestamps.notna()
            before_start = valid_timestamps & starts.notna() & timestamps.lt(starts)
            after_end = valid_timestamps & ends.notna() & timestamps.gt(ends)
            valid_rows = (
                valid_timestamps
                & starts.notna()
                & ends.notna()
                & timestamps.ge(starts)
                & timestamps.le(ends)
            )
            filtered = chunk.loc[valid_rows, original_columns]
            filtered.to_csv(
                temporary_path,
                mode="w" if not wrote_header else "a",
                header=not wrote_header,
                index=False,
            )
            wrote_header = True
            total_rows += len(chunk)
            kept_rows += int(valid_rows.sum())
            invalid_timestamp_rows += int((~valid_timestamps).sum())
            before_start_rows += int(before_start.sum())
            after_end_rows += int(after_end.sum())

        if unknown_games:
            raise ValueError(
                f"{input_path} contains unmapped game IDs: {sorted(unknown_games)}"
            )
        if missing_game_id_rows:
            raise ValueError(
                f"{input_path} contains {missing_game_id_rows} rows without game_id"
            )

        if not wrote_header:
            header.to_csv(temporary_path, index=False)

        os.replace(temporary_path, output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise

    return (
        total_rows,
        kept_rows,
        before_start_rows,
        after_end_rows,
        invalid_timestamp_rows,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=SCRIPT_PROJECT_ROOT)
    parser.add_argument("--chunksize", type=int, default=200_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    market_map_path = root / "data" / "matched_markets" / "MM_market_map.csv"
    cutoffs_path = root / "data" / "game_cutoffs.csv"
    try:
        market_map = pd.read_csv(market_map_path, dtype="string")
        cutoffs = pd.read_csv(cutoffs_path)
        windows = map_games(market_map, cutoffs)
        fuzzy = windows.loc[windows["match_method"] == "fuzzy_title"]
        print(f"Matched {len(windows)} game IDs to ESPN cutoffs.")
        if not fuzzy.empty:
            print("Fuzzy title matches used:")
            for row in fuzzy.itertuples(index=False):
                print(
                    f"  {row.game_id}: {row.market_game} -> {row.espn_game} "
                    f"({row.match_score:.3f})"
                )

        for platform in ["kalshi", "polymarket"]:
            platform_directory = root / "data" / "processed" / platform
            output_directory = platform_directory / "filtered_bbo_trades"
            jobs = [
                (
                    "BBO",
                    platform_directory / "bbo.csv",
                    output_directory / "bbo_filtered.csv",
                ),
                (
                    "Trades",
                    platform_directory / "trades.csv",
                    output_directory / "trades_filtered.csv",
                ),
            ]
            print(f"\n{platform.upper()}")
            for label, input_path, output_path in jobs:
                total, kept, before, after, invalid = filter_file(
                    input_path,
                    output_path,
                    windows,
                    chunksize=args.chunksize,
                )
                print(
                    f"{label}: kept {kept:,} of {total:,}; "
                    f"before game {before:,}; after game {after:,}; "
                    f"invalid timestamps {invalid:,}"
                )
                print(f"Saved: {output_path}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())