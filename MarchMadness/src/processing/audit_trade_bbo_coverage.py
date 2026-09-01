from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from polymarket_bbo_trades_timestampfilter import map_games, timestamps_to_ns


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "data/results"
MARKET_MAP_PATH = ROOT / "data/matched_markets/MM_market_map.csv"
CUTOFFS_PATH = ROOT / "data/game_cutoffs.csv"

BBO_PATHS = {
    "kalshi": ROOT
    / "data/processed/kalshi/filtered_bbo_trades/bbo_filtered.csv",
    "polymarket": ROOT
    / "data/processed/polymarket/filtered_bbo_trades/bbo_filtered.csv",
}

TRADE_PATHS = {
    "kalshi": ROOT
    / "data/processed/kalshi/filtered_bbo_trades/trades_filtered.csv",
    "polymarket": ROOT
    / "data/processed/polymarket/filtered_bbo_trades/trades_filtered.csv",
}

INSTRUMENT_OUTPUT = RESULTS_DIR / "data_coverage_by_instrument.csv"
GAME_PLATFORM_OUTPUT = RESULTS_DIR / "data_coverage_by_game_platform.csv"
PAIRED_GAME_OUTPUT = RESULTS_DIR / "data_coverage_paired_games.csv"
UNEXPECTED_OUTPUT = RESULTS_DIR / "data_coverage_unexpected_instruments.csv"

CHUNK_SIZE = 200_000
RECENT_BBO_THRESHOLDS_SECONDS = (5, 30, 60, 300)
LONG_GAP_SECONDS = 300
MIN_TRADES_WITH_BBO_WITHIN_60S_PERCENT = 90.0


def require_file(path):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def empty_bbo_stats():
    return {
        "rows": 0,
        "valid_timestamp_rows": 0,
        "missing_side_rows": 0,
        "nonpositive_side_rows": 0,
        "out_of_range_rows": 0,
        "crossed_rows": 0,
        "valid_rows": 0,
    }


def empty_trade_stats():
    return {
        "rows": 0,
        "valid_timestamp_rows": 0,
        "missing_price_or_size_rows": 0,
        "out_of_range_price_rows": 0,
        "nonpositive_size_rows": 0,
        "valid_rows": 0,
    }


def load_expected_instruments():
    require_file(MARKET_MAP_PATH)
    require_file(CUTOFFS_PATH)

    market_map = pd.read_csv(MARKET_MAP_PATH, dtype="string")
    cutoffs = pd.read_csv(CUTOFFS_PATH)
    windows = map_games(market_map.copy(), cutoffs)
    windows = windows[
        ["game_id", "espn_game", "start_ns", "end_ns"]
    ].copy()

    definitions = [
        ("kalshi", "kalshi_ticker_1", "kalshi_team_1"),
        ("kalshi", "kalshi_ticker_2", "kalshi_team_2"),
        ("polymarket", "polymarket_token_id_1", "polymarket_team_a"),
        ("polymarket", "polymarket_token_id_2", "polymarket_team_b"),
    ]

    parts = []
    for platform, instrument_column, team_column in definitions:
        if instrument_column not in market_map.columns:
            raise ValueError(f"Market map is missing {instrument_column}")
        team = (
            market_map[team_column].astype("string")
            if team_column in market_map.columns
            else pd.Series(pd.NA, index=market_map.index, dtype="string")
        )
        parts.append(
            pd.DataFrame(
                {
                    "platform": platform,
                    "game_id": market_map["game_id"].astype("string"),
                    "team": team,
                    "instrument_id": market_map[instrument_column].astype("string"),
                }
            )
        )

    expected = pd.concat(parts, ignore_index=True)
    expected["game_id"] = expected["game_id"].str.strip()
    expected["instrument_id"] = expected["instrument_id"].str.strip()
    expected = expected.dropna(subset=["game_id", "instrument_id"])
    expected = expected.drop_duplicates(
        subset=["platform", "game_id", "instrument_id"]
    )
    expected = expected.merge(windows, on="game_id", how="left", validate="many_to_one")

    if expected[["start_ns", "end_ns"]].isna().any().any():
        raise ValueError("Some expected instruments are missing ESPN game windows")

    duplicate_ids = expected.duplicated(
        subset=["platform", "instrument_id"], keep=False
    )
    if duplicate_ids.any():
        rows = expected.loc[
            duplicate_ids, ["platform", "game_id", "instrument_id"]
        ]
        raise ValueError(
            "Instrument IDs map to multiple games:\n" + rows.to_string(index=False)
        )

    return expected.sort_values(["game_id", "platform", "team"])


def normalize_price_series(values, platform):
    numeric = pd.to_numeric(values, errors="coerce")
    nonmissing = numeric.dropna()
    if platform == "kalshi" and not nonmissing.empty and nonmissing.median() > 1:
        numeric = numeric / 100.0
    return numeric


def scan_bbo_file(platform, path):
    require_file(path)
    header = pd.read_csv(path, nrows=0)
    required = {
        "game_id",
        "instrument_id",
        "timestamp",
        "best_bid",
        "best_ask",
    }
    missing = required - set(header.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")

    stats = defaultdict(empty_bbo_stats)
    valid_times = defaultdict(list)

    for chunk in pd.read_csv(
        path,
        usecols=list(required),
        chunksize=CHUNK_SIZE,
        low_memory=False,
        dtype={"game_id": "string", "instrument_id": "string"},
    ):
        chunk["game_id"] = chunk["game_id"].str.strip()
        chunk["instrument_id"] = chunk["instrument_id"].str.strip()
        chunk["timestamp_ns"] = timestamps_to_ns(chunk["timestamp"])
        chunk["best_bid"] = normalize_price_series(chunk["best_bid"], platform)
        chunk["best_ask"] = normalize_price_series(chunk["best_ask"], platform)

        timestamp_valid = chunk["timestamp_ns"].notna()
        missing_side = chunk["best_bid"].isna() | chunk["best_ask"].isna()
        nonpositive_side = (
            chunk["best_bid"].notna()
            & chunk["best_ask"].notna()
            & (chunk["best_bid"].le(0) | chunk["best_ask"].le(0))
        )
        out_of_range = (
            chunk["best_bid"].notna()
            & chunk["best_ask"].notna()
            & (
                chunk["best_bid"].gt(1)
                | chunk["best_ask"].gt(1)
                | chunk["best_bid"].lt(0)
                | chunk["best_ask"].lt(0)
            )
        )
        crossed = (
            chunk["best_bid"].notna()
            & chunk["best_ask"].notna()
            & chunk["best_bid"].gt(chunk["best_ask"])
        )
        valid = (
            timestamp_valid
            & ~missing_side
            & chunk["best_bid"].gt(0)
            & chunk["best_ask"].gt(0)
            & chunk["best_bid"].le(1)
            & chunk["best_ask"].le(1)
            & ~crossed
        )

        chunk["timestamp_valid"] = timestamp_valid
        chunk["missing_side"] = missing_side
        chunk["nonpositive_side"] = nonpositive_side
        chunk["out_of_range"] = out_of_range
        chunk["crossed"] = crossed
        chunk["valid"] = valid

        for (game_id, instrument_id), group in chunk.groupby(
            ["game_id", "instrument_id"], dropna=False, sort=False
        ):
            key = (platform, str(game_id), str(instrument_id))
            item = stats[key]
            item["rows"] += len(group)
            item["valid_timestamp_rows"] += int(group["timestamp_valid"].sum())
            item["missing_side_rows"] += int(group["missing_side"].sum())
            item["nonpositive_side_rows"] += int(
                group["nonpositive_side"].sum()
            )
            item["out_of_range_rows"] += int(group["out_of_range"].sum())
            item["crossed_rows"] += int(group["crossed"].sum())
            item["valid_rows"] += int(group["valid"].sum())
            times = group.loc[group["valid"], "timestamp_ns"]
            if not times.empty:
                valid_times[key].append(times.astype("int64").to_numpy())

    return stats, valid_times


def scan_trade_file(platform, path):
    require_file(path)
    header = pd.read_csv(path, nrows=0)
    required = {"game_id", "instrument_id", "timestamp", "price", "size"}
    missing = required - set(header.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")

    stats = defaultdict(empty_trade_stats)
    valid_times = defaultdict(list)

    for chunk in pd.read_csv(
        path,
        usecols=list(required),
        chunksize=CHUNK_SIZE,
        low_memory=False,
        dtype={"game_id": "string", "instrument_id": "string"},
    ):
        chunk["game_id"] = chunk["game_id"].str.strip()
        chunk["instrument_id"] = chunk["instrument_id"].str.strip()
        chunk["timestamp_ns"] = timestamps_to_ns(chunk["timestamp"])
        chunk["price"] = normalize_price_series(chunk["price"], platform)
        chunk["size"] = pd.to_numeric(chunk["size"], errors="coerce")

        timestamp_valid = chunk["timestamp_ns"].notna()
        missing_price_or_size = chunk["price"].isna() | chunk["size"].isna()
        out_of_range_price = chunk["price"].notna() & (
            chunk["price"].lt(0) | chunk["price"].gt(1)
        )
        nonpositive_size = chunk["size"].notna() & chunk["size"].le(0)
        valid = (
            timestamp_valid
            & ~missing_price_or_size
            & chunk["price"].ge(0)
            & chunk["price"].le(1)
            & chunk["size"].gt(0)
        )

        chunk["timestamp_valid"] = timestamp_valid
        chunk["missing_price_or_size"] = missing_price_or_size
        chunk["out_of_range_price"] = out_of_range_price
        chunk["nonpositive_size"] = nonpositive_size
        chunk["valid"] = valid

        for (game_id, instrument_id), group in chunk.groupby(
            ["game_id", "instrument_id"], dropna=False, sort=False
        ):
            key = (platform, str(game_id), str(instrument_id))
            item = stats[key]
            item["rows"] += len(group)
            item["valid_timestamp_rows"] += int(group["timestamp_valid"].sum())
            item["missing_price_or_size_rows"] += int(
                group["missing_price_or_size"].sum()
            )
            item["out_of_range_price_rows"] += int(
                group["out_of_range_price"].sum()
            )
            item["nonpositive_size_rows"] += int(
                group["nonpositive_size"].sum()
            )
            item["valid_rows"] += int(group["valid"].sum())
            times = group.loc[group["valid"], "timestamp_ns"]
            if not times.empty:
                valid_times[key].append(times.astype("int64").to_numpy())

    return stats, valid_times


def combine_times(parts):
    if not parts:
        return np.array([], dtype=np.int64)
    if len(parts) == 1:
        return np.sort(parts[0].astype(np.int64, copy=False))
    return np.sort(np.concatenate(parts).astype(np.int64, copy=False))


def timestamp_metrics(times, start_ns, end_ns, prefix):
    duration_minutes = max(1, int(np.ceil((end_ns - start_ns) / 60_000_000_000)))
    if len(times) == 0:
        return {
            f"{prefix}_first_timestamp": pd.NaT,
            f"{prefix}_last_timestamp": pd.NaT,
            f"{prefix}_start_delay_seconds": np.nan,
            f"{prefix}_end_early_seconds": np.nan,
            f"{prefix}_median_gap_seconds": np.nan,
            f"{prefix}_p95_gap_seconds": np.nan,
            f"{prefix}_max_gap_seconds": np.nan,
            f"{prefix}_active_minutes": 0,
            f"{prefix}_active_minute_coverage_percent": 0.0,
        }

    unique_times = np.unique(times)
    first = int(unique_times[0])
    last = int(unique_times[-1])
    gaps = np.diff(unique_times) / 1_000_000_000
    active_minutes = len(np.unique(unique_times // 60_000_000_000))

    return {
        f"{prefix}_first_timestamp": pd.to_datetime(first, unit="ns", utc=True),
        f"{prefix}_last_timestamp": pd.to_datetime(last, unit="ns", utc=True),
        f"{prefix}_start_delay_seconds": (first - start_ns) / 1_000_000_000,
        f"{prefix}_end_early_seconds": (end_ns - last) / 1_000_000_000,
        f"{prefix}_median_gap_seconds": (
            float(np.median(gaps)) if len(gaps) else np.nan
        ),
        f"{prefix}_p95_gap_seconds": (
            float(np.percentile(gaps, 95)) if len(gaps) else np.nan
        ),
        f"{prefix}_max_gap_seconds": float(gaps.max()) if len(gaps) else np.nan,
        f"{prefix}_active_minutes": active_minutes,
        f"{prefix}_active_minute_coverage_percent": min(
            100.0, active_minutes / duration_minutes * 100
        ),
    }


def trade_bbo_metrics(trade_times, bbo_times):
    result = {
        "trades_with_any_prior_bbo_percent": np.nan,
        "bbo_age_at_trade_median_seconds": np.nan,
        "bbo_age_at_trade_p95_seconds": np.nan,
        "bbo_age_at_trade_max_seconds": np.nan,
    }
    for threshold in RECENT_BBO_THRESHOLDS_SECONDS:
        result[f"trades_with_bbo_within_{threshold}s_percent"] = np.nan

    if len(trade_times) == 0:
        return result
    if len(bbo_times) == 0:
        result["trades_with_any_prior_bbo_percent"] = 0.0
        for threshold in RECENT_BBO_THRESHOLDS_SECONDS:
            result[f"trades_with_bbo_within_{threshold}s_percent"] = 0.0
        return result

    bbo_times = np.sort(bbo_times)
    trade_times = np.sort(trade_times)
    positions = np.searchsorted(bbo_times, trade_times, side="right") - 1
    has_prior = positions >= 0
    result["trades_with_any_prior_bbo_percent"] = float(has_prior.mean() * 100)

    if not has_prior.any():
        for threshold in RECENT_BBO_THRESHOLDS_SECONDS:
            result[f"trades_with_bbo_within_{threshold}s_percent"] = 0.0
        return result

    ages = np.full(len(trade_times), np.inf, dtype=float)
    ages[has_prior] = (
        trade_times[has_prior] - bbo_times[positions[has_prior]]
    ) / 1_000_000_000
    finite_ages = ages[np.isfinite(ages)]
    result["bbo_age_at_trade_median_seconds"] = float(np.median(finite_ages))
    result["bbo_age_at_trade_p95_seconds"] = float(
        np.percentile(finite_ages, 95)
    )
    result["bbo_age_at_trade_max_seconds"] = float(finite_ages.max())

    for threshold in RECENT_BBO_THRESHOLDS_SECONDS:
        result[f"trades_with_bbo_within_{threshold}s_percent"] = float(
            np.mean(ages <= threshold) * 100
        )

    return result


def coverage_status(bbo, trades):
    if bbo["rows"] == 0 and trades["rows"] == 0:
        return "missing_bbo_and_trades"
    if bbo["rows"] == 0:
        return "missing_bbo"
    if bbo["valid_rows"] == 0:
        return "bbo_present_but_no_valid_rows"
    if trades["rows"] == 0:
        return "missing_trades"
    if trades["valid_rows"] == 0:
        return "trades_present_but_no_valid_rows"
    return "bbo_and_trades_present"


def quality_flags(record):
    flags = []
    if record["bbo_rows"] == 0:
        flags.append("no_bbo_rows")
    elif record["bbo_valid_rows"] == 0:
        flags.append("no_valid_bbo_rows")
    if record["trade_rows"] == 0:
        flags.append("no_trade_rows")
    elif record["trade_valid_rows"] == 0:
        flags.append("no_valid_trade_rows")
    if record["bbo_crossed_rows"] > 0:
        flags.append("crossed_bbo_rows")
    if record["bbo_missing_side_rows"] + record["bbo_nonpositive_side_rows"] > 0:
        flags.append("one_sided_bbo_rows")
    if (
        pd.notna(record["bbo_max_gap_seconds"])
        and record["bbo_max_gap_seconds"] > LONG_GAP_SECONDS
    ):
        flags.append("bbo_gap_over_300s")
    coverage_60 = record["trades_with_bbo_within_60s_percent"]
    if (
        record["trade_valid_rows"] > 0
        and pd.notna(coverage_60)
        and coverage_60 < MIN_TRADES_WITH_BBO_WITHIN_60S_PERCENT
    ):
        flags.append("under_90pct_trades_have_bbo_within_60s")
    return ";".join(flags) if flags else "none"


def build_instrument_coverage(
    expected,
    bbo_stats,
    bbo_times,
    trade_stats,
    trade_times,
):
    records = []

    for row in expected.itertuples(index=False):
        key = (row.platform, str(row.game_id), str(row.instrument_id))
        bbo = bbo_stats.get(key, empty_bbo_stats())
        trades = trade_stats.get(key, empty_trade_stats())
        bbo_timestamp_values = combine_times(bbo_times.get(key, []))
        trade_timestamp_values = combine_times(trade_times.get(key, []))
        start_ns = int(row.start_ns)
        end_ns = int(row.end_ns)

        record = {
            "platform": row.platform,
            "game_id": row.game_id,
            "espn_game": row.espn_game,
            "team": row.team,
            "instrument_id": row.instrument_id,
            "game_start_utc": pd.to_datetime(start_ns, unit="ns", utc=True),
            "game_end_utc": pd.to_datetime(end_ns, unit="ns", utc=True),
            "game_duration_minutes": (end_ns - start_ns) / 60_000_000_000,
        }
        record.update({f"bbo_{name}": value for name, value in bbo.items()})
        record.update({f"trade_{name}": value for name, value in trades.items()})
        record.update(
            timestamp_metrics(bbo_timestamp_values, start_ns, end_ns, "bbo")
        )
        record.update(
            timestamp_metrics(trade_timestamp_values, start_ns, end_ns, "trade")
        )
        record.update(trade_bbo_metrics(trade_timestamp_values, bbo_timestamp_values))
        record["coverage_status"] = coverage_status(bbo, trades)
        record["bbo_eligible"] = bbo["valid_rows"] > 0
        record["trade_eligible"] = trades["valid_rows"] > 0
        record["joint_eligible"] = (
            record["bbo_eligible"] and record["trade_eligible"]
        )
        coverage_60 = record["trades_with_bbo_within_60s_percent"]
        record["joint_60s_coverage_eligible"] = (
            record["joint_eligible"]
            and pd.notna(coverage_60)
            and coverage_60 >= MIN_TRADES_WITH_BBO_WITHIN_60S_PERCENT
        )
        record["quality_flags"] = quality_flags(record)
        records.append(record)

    return pd.DataFrame(records).sort_values(
        ["game_id", "platform", "team", "instrument_id"]
    )


def build_game_platform_coverage(instruments):
    records = []
    for (game_id, platform), group in instruments.groupby(
        ["game_id", "platform"], sort=True
    ):
        records.append(
            {
                "game_id": game_id,
                "platform": platform,
                "espn_game": group["espn_game"].iloc[0],
                "expected_instruments": len(group),
                "bbo_eligible_instruments": int(group["bbo_eligible"].sum()),
                "trade_eligible_instruments": int(group["trade_eligible"].sum()),
                "joint_eligible_instruments": int(group["joint_eligible"].sum()),
                "joint_60s_coverage_eligible_instruments": int(
                    group["joint_60s_coverage_eligible"].sum()
                ),
                "complete_bbo_game_platform": bool(group["bbo_eligible"].all()),
                "complete_trade_game_platform": bool(
                    group["trade_eligible"].all()
                ),
                "complete_joint_game_platform": bool(
                    group["joint_eligible"].all()
                ),
                "complete_joint_60s_game_platform": bool(
                    group["joint_60s_coverage_eligible"].all()
                ),
                "total_bbo_rows": int(group["bbo_rows"].sum()),
                "total_valid_bbo_rows": int(group["bbo_valid_rows"].sum()),
                "total_trade_rows": int(group["trade_rows"].sum()),
                "total_valid_trade_rows": int(group["trade_valid_rows"].sum()),
                "minimum_trades_with_bbo_within_60s_percent": group[
                    "trades_with_bbo_within_60s_percent"
                ].min(),
                "instrument_quality_flags": " | ".join(
                    f"{team}: {flags}"
                    for team, flags in zip(group["team"], group["quality_flags"])
                    if flags != "none"
                )
                or "none",
            }
        )
    return pd.DataFrame(records).sort_values(["game_id", "platform"])


def build_paired_game_coverage(game_platform):
    records = []
    for game_id, group in game_platform.groupby("game_id", sort=True):
        platforms = set(group["platform"])
        has_both = platforms == {"kalshi", "polymarket"}
        records.append(
            {
                "game_id": game_id,
                "espn_game": group["espn_game"].iloc[0],
                "platforms_present": ",".join(sorted(platforms)),
                "paired_bbo_eligible": bool(
                    has_both and group["complete_bbo_game_platform"].all()
                ),
                "paired_trade_eligible": bool(
                    has_both and group["complete_trade_game_platform"].all()
                ),
                "paired_joint_eligible": bool(
                    has_both and group["complete_joint_game_platform"].all()
                ),
                "paired_joint_60s_eligible": bool(
                    has_both and group["complete_joint_60s_game_platform"].all()
                ),
                "platform_coverage": " | ".join(
                    f"{row.platform}: BBO {row.bbo_eligible_instruments}/{row.expected_instruments}, "
                    f"trades {row.trade_eligible_instruments}/{row.expected_instruments}, "
                    f"joint60s {row.joint_60s_coverage_eligible_instruments}/{row.expected_instruments}"
                    for row in group.itertuples(index=False)
                ),
            }
        )
    return pd.DataFrame(records).sort_values("game_id")


def build_unexpected(expected_keys, bbo_stats, trade_stats):
    observed_keys = set(bbo_stats) | set(trade_stats)
    rows = []
    for key in sorted(observed_keys - expected_keys):
        platform, game_id, instrument_id = key
        bbo = bbo_stats.get(key, empty_bbo_stats())
        trades = trade_stats.get(key, empty_trade_stats())
        rows.append(
            {
                "platform": platform,
                "game_id": game_id,
                "instrument_id": instrument_id,
                "bbo_rows": bbo["rows"],
                "bbo_valid_rows": bbo["valid_rows"],
                "trade_rows": trades["rows"],
                "trade_valid_rows": trades["valid_rows"],
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "platform",
            "game_id",
            "instrument_id",
            "bbo_rows",
            "bbo_valid_rows",
            "trade_rows",
            "trade_valid_rows",
        ],
    )


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    expected = load_expected_instruments()

    all_bbo_stats = {}
    all_bbo_times = {}
    all_trade_stats = {}
    all_trade_times = {}

    for platform, path in BBO_PATHS.items():
        print(f"Scanning {platform} BBO: {path}")
        stats, times = scan_bbo_file(platform, path)
        all_bbo_stats.update(stats)
        all_bbo_times.update(times)

    for platform, path in TRADE_PATHS.items():
        print(f"Scanning {platform} trades: {path}")
        stats, times = scan_trade_file(platform, path)
        all_trade_stats.update(stats)
        all_trade_times.update(times)

    instruments = build_instrument_coverage(
        expected,
        all_bbo_stats,
        all_bbo_times,
        all_trade_stats,
        all_trade_times,
    )
    game_platform = build_game_platform_coverage(instruments)
    paired_games = build_paired_game_coverage(game_platform)
    expected_keys = {
        (row.platform, str(row.game_id), str(row.instrument_id))
        for row in expected.itertuples(index=False)
    }
    unexpected = build_unexpected(
        expected_keys,
        all_bbo_stats,
        all_trade_stats,
    )

    instruments.to_csv(INSTRUMENT_OUTPUT, index=False)
    game_platform.to_csv(GAME_PLATFORM_OUTPUT, index=False)
    paired_games.to_csv(PAIRED_GAME_OUTPUT, index=False)
    unexpected.to_csv(UNEXPECTED_OUTPUT, index=False)

    print()
    print(f"Expected instruments: {len(instruments):,}")
    print(f"Instruments with no BBO rows: {(instruments['bbo_rows'] == 0).sum():,}")
    print(
        "Instruments with BBO rows but no valid BBO rows:",
        f"{((instruments['bbo_rows'] > 0) & (instruments['bbo_valid_rows'] == 0)).sum():,}",
    )
    print(f"Instruments with no trade rows: {(instruments['trade_rows'] == 0).sum():,}")
    print(
        "Instruments with trade rows but no valid trade rows:",
        f"{((instruments['trade_rows'] > 0) & (instruments['trade_valid_rows'] == 0)).sum():,}",
    )
    print(f"BBO-eligible instruments: {instruments['bbo_eligible'].sum():,}")
    print(f"Trade-eligible instruments: {instruments['trade_eligible'].sum():,}")
    print(f"Joint-eligible instruments: {instruments['joint_eligible'].sum():,}")
    print(
        "Joint instruments with at least 90% of trades covered by a prior BBO within 60s:",
        f"{instruments['joint_60s_coverage_eligible'].sum():,}",
    )
    print(
        "Paired games eligible for BBO comparison:",
        f"{paired_games['paired_bbo_eligible'].sum():,}/{len(paired_games):,}",
    )
    print(
        "Paired games eligible for trade comparison:",
        f"{paired_games['paired_trade_eligible'].sum():,}/{len(paired_games):,}",
    )
    print(
        "Paired games eligible for joint BBO/trade comparison:",
        f"{paired_games['paired_joint_eligible'].sum():,}/{len(paired_games):,}",
    )
    print(f"Unexpected instrument keys: {len(unexpected):,}")

    severe = instruments[
        (instruments["bbo_rows"] == 0)
        | (instruments["bbo_valid_rows"] == 0)
        | (instruments["trade_rows"] == 0)
        | (instruments["trade_valid_rows"] == 0)
    ]
    if not severe.empty:
        print()
        print("Missing or fully invalid instruments:")
        print(
            severe[
                [
                    "platform",
                    "game_id",
                    "team",
                    "instrument_id",
                    "bbo_rows",
                    "bbo_valid_rows",
                    "trade_rows",
                    "trade_valid_rows",
                    "coverage_status",
                ]
            ].to_string(index=False)
        )

    print()
    print(f"Saved: {INSTRUMENT_OUTPUT}")
    print(f"Saved: {GAME_PLATFORM_OUTPUT}")
    print(f"Saved: {PAIRED_GAME_OUTPUT}")
    print(f"Saved: {UNEXPECTED_OUTPUT}")


if __name__ == "__main__":
    main()