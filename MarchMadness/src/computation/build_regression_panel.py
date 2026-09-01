from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUCKET_SECONDS = (60, 300)
CHUNK_SIZE = 500_000
LOG_ODDS_CLIP = 0.005


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    normalized = series.astype("string").str.strip().str.lower()
    unexpected = ~normalized.isin(
        ["true", "1", "yes", "false", "0", "no", "", "<na>"]
    )
    if unexpected.any():
        values = sorted(normalized.loc[unexpected].dropna().unique())
        raise ValueError(f"Could not parse boolean values: {values}")
    return normalized.isin(["true", "1", "yes"])


def parse_timestamp_column(series: pd.Series) -> pd.Series:
    raw = series.astype("string").str.strip()
    numeric = pd.to_numeric(raw, errors="coerce")
    absolute = numeric.abs()
    parsed = pd.Series(
        pd.NaT,
        index=series.index,
        dtype="datetime64[ns, UTC]",
    )

    unit_masks = {
        "s": numeric.notna() & absolute.lt(100_000_000_000),
        "ms": (
            numeric.notna()
            & absolute.ge(100_000_000_000)
            & absolute.lt(100_000_000_000_000)
        ),
        "us": (
            numeric.notna()
            & absolute.ge(100_000_000_000_000)
            & absolute.lt(100_000_000_000_000_000)
        ),
        "ns": numeric.notna() & absolute.ge(100_000_000_000_000_000),
    }
    for unit, mask in unit_masks.items():
        if mask.any():
            parsed.loc[mask] = pd.to_datetime(
                numeric.loc[mask],
                unit=unit,
                utc=True,
                errors="coerce",
            )

    text_mask = numeric.isna() & raw.notna() & raw.ne("")
    if text_mask.any():
        try:
            text_parsed = pd.to_datetime(
                raw.loc[text_mask],
                format="mixed",
                utc=True,
                errors="coerce",
            )
        except TypeError:
            text_parsed = pd.to_datetime(
                raw.loc[text_mask],
                utc=True,
                errors="coerce",
            )
        parsed.loc[text_mask] = text_parsed
    return parsed


def load_selection(path: Path) -> pd.DataFrame:
    require_file(path)
    selection = pd.read_csv(
        path,
        dtype={
            "game_id": "string",
            "selected_team": "string",
            "kalshi_instrument_id": "string",
            "polymarket_instrument_id": "string",
        },
    )
    required = {
        "game_id",
        "selected_team",
        "kalshi_instrument_id",
        "polymarket_instrument_id",
        "bbo_sample_eligible",
        "joint_sample_eligible",
    }
    missing = required - set(selection.columns)
    if missing:
        raise ValueError(
            f"Selected-team file is missing columns: {sorted(missing)}"
        )
    if len(selection) != 67 or selection["game_id"].nunique() != 67:
        raise ValueError("Expected exactly 67 unique selected games")

    selection["bbo_sample_eligible"] = as_bool(
        selection["bbo_sample_eligible"]
    )
    selection["joint_sample_eligible"] = as_bool(
        selection["joint_sample_eligible"]
    )
    eligible = selection.loc[
        selection["bbo_sample_eligible"]
        & selection["joint_sample_eligible"]
    ].copy()
    if len(eligible) != 64:
        raise ValueError(
            f"Expected 64 BBO/joint-eligible games; found {len(eligible)}"
        )

    records: list[dict[str, str]] = []
    for row in eligible.itertuples(index=False):
        records.extend(
            [
                {
                    "game_id": str(row.game_id),
                    "selected_team": str(row.selected_team),
                    "platform": "kalshi",
                    "instrument_id": str(row.kalshi_instrument_id),
                },
                {
                    "game_id": str(row.game_id),
                    "selected_team": str(row.selected_team),
                    "platform": "polymarket",
                    "instrument_id": str(row.polymarket_instrument_id),
                },
            ]
        )
    keys = pd.DataFrame(records)
    if len(keys) != 128 or keys.duplicated(
        ["game_id", "platform", "instrument_id"]
    ).any():
        raise ValueError("Selected regression keys are not unique")
    return keys


def key_strings(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["game_id"].astype("string")
        + "\x1f"
        + frame["instrument_id"].astype("string")
    )


def normalize_price_columns(
    frame: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    frame = frame.copy()
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        values = frame[column].dropna()
        if not values.empty and values.median() > 1:
            frame[column] = frame[column] / 100.0
    return frame


def load_selected_bbo(
    paths: dict[str, Path],
    keys: pd.DataFrame,
) -> pd.DataFrame:
    required = [
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "timestamp",
        "best_bid",
        "best_ask",
    ]
    parts: list[pd.DataFrame] = []
    for platform, path in paths.items():
        require_file(path)
        selected_keys = set(
            key_strings(keys.loc[keys["platform"].eq(platform)]).tolist()
        )
        for chunk in pd.read_csv(
            path,
            usecols=required,
            chunksize=CHUNK_SIZE,
            low_memory=False,
            dtype={
                "game_id": "string",
                "platform": "string",
                "team": "string",
                "instrument_id": "string",
            },
        ):
            chunk = chunk.loc[key_strings(chunk).isin(selected_keys)].copy()
            if chunk.empty:
                continue
            if not chunk["platform"].eq(platform).all():
                raise ValueError(f"{path} contains unexpected platform values")
            chunk["timestamp"] = parse_timestamp_column(chunk["timestamp"])
            chunk = normalize_price_columns(
                chunk,
                ["best_bid", "best_ask"],
            )
            chunk = chunk.dropna(
                subset=["timestamp", "best_bid", "best_ask"]
            )
            chunk = chunk.loc[
                chunk["best_bid"].gt(0)
                & chunk["best_ask"].gt(0)
                & chunk["best_bid"].le(1)
                & chunk["best_ask"].le(1)
                & chunk["best_bid"].le(chunk["best_ask"])
            ].copy()
            chunk["mid_price"] = (
                chunk["best_bid"] + chunk["best_ask"]
            ) / 2.0
            parts.append(chunk)
    if not parts:
        raise ValueError("No selected BBO observations were loaded")
    bbo = pd.concat(parts, ignore_index=True)
    bbo = bbo.merge(
        keys,
        on=["game_id", "platform", "instrument_id"],
        how="inner",
        validate="many_to_one",
        suffixes=("_source", ""),
    )
    return bbo.sort_values(
        ["game_id", "platform", "instrument_id", "timestamp"]
    ).reset_index(drop=True)


def load_selected_trades(
    paths: dict[str, Path],
    keys: pd.DataFrame,
) -> pd.DataFrame:
    required = [
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "trade_id",
        "timestamp",
        "size",
        "side",
    ]
    parts: list[pd.DataFrame] = []
    for platform, path in paths.items():
        require_file(path)
        selected_keys = set(
            key_strings(keys.loc[keys["platform"].eq(platform)]).tolist()
        )
        for chunk in pd.read_csv(
            path,
            usecols=required,
            chunksize=CHUNK_SIZE,
            low_memory=False,
            dtype={
                "game_id": "string",
                "platform": "string",
                "team": "string",
                "instrument_id": "string",
                "trade_id": "string",
            },
        ):
            chunk = chunk.loc[key_strings(chunk).isin(selected_keys)].copy()
            if chunk.empty:
                continue
            if not chunk["platform"].eq(platform).all():
                raise ValueError(f"{path} contains unexpected platform values")
            chunk["timestamp"] = parse_timestamp_column(chunk["timestamp"])
            chunk["size"] = pd.to_numeric(chunk["size"], errors="coerce")
            side = chunk["side"].astype("string").str.strip().str.upper()
            if platform == "kalshi":
                valid_side = side.isin(["YES", "NO"])
                chunk["signed_size"] = np.where(
                    side.eq("YES"),
                    chunk["size"],
                    -chunk["size"],
                )
            else:
                valid_side = side.isin(["BUY", "SELL"])
                # Predexon's Polymarket `side` is the maker's side.
                # is a taker sell and a maker SELL is a taker buy.
                chunk["signed_size"] = np.where(
                    side.eq("SELL"),
                    chunk["size"],
                    -chunk["size"],
                )
            if not valid_side.all():
                unexpected = sorted(side.loc[~valid_side].dropna().unique())
                raise ValueError(
                    f"{path} has unexpected trade sides: {unexpected}"
                )
            chunk = chunk.dropna(subset=["timestamp", "size"])
            chunk = chunk.loc[chunk["size"].gt(0)].copy()
            parts.append(chunk)
    if not parts:
        raise ValueError("No selected trades were loaded")
    trades = pd.concat(parts, ignore_index=True)
    trades = trades.merge(
        keys,
        on=["game_id", "platform", "instrument_id"],
        how="inner",
        validate="many_to_one",
        suffixes=("_source", ""),
    )
    return trades.sort_values(
        ["game_id", "platform", "instrument_id", "timestamp"]
    ).reset_index(drop=True)


def ceil_epoch_ns(values: pd.Series, bucket_ns: int) -> np.ndarray:
    timestamps = values.astype("int64").to_numpy()
    return ((timestamps + bucket_ns - 1) // bucket_ns) * bucket_ns


def build_group_panel(
    key: pd.Series,
    bbo: pd.DataFrame,
    trades: pd.DataFrame,
    bucket_seconds: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    bucket_ns = int(bucket_seconds * 1_000_000_000)
    base = {
        "bucket_seconds": bucket_seconds,
        "game_id": key["game_id"],
        "selected_team": key["selected_team"],
        "platform": key["platform"],
        "instrument_id": key["instrument_id"],
        "bbo_rows": len(bbo),
        "trade_rows": len(trades),
    }
    if bbo.empty:
        return pd.DataFrame(), {
            **base,
            "first_bbo_timestamp": pd.NaT,
            "last_bbo_timestamp": pd.NaT,
            "bbo_span_minutes": np.nan,
            "panel_rows": 0,
            "nonzero_flow_buckets": 0,
            "trade_rows_in_panel_window": 0,
            "trade_rows_outside_panel_window": len(trades),
            "status": "no_valid_bbo",
        }

    work_bbo = bbo.sort_values("timestamp").copy()
    work_bbo["bucket_end_ns"] = ceil_epoch_ns(
        work_bbo["timestamp"],
        bucket_ns,
    )
    last_in_bucket = work_bbo.groupby(
        "bucket_end_ns",
        sort=True,
    ).tail(1)
    mid_by_bucket = last_in_bucket.set_index("bucket_end_ns")["mid_price"]
    updates_by_bucket = work_bbo.groupby("bucket_end_ns").size()

    first_bucket = int(mid_by_bucket.index.min())
    last_bucket = int(mid_by_bucket.index.max())
    grid = np.arange(
        first_bucket,
        last_bucket + bucket_ns,
        bucket_ns,
        dtype=np.int64,
    )
    mid_end = mid_by_bucket.reindex(grid).ffill()
    bbo_updates = updates_by_bucket.reindex(grid, fill_value=0)

    work_trades = trades.copy()
    if not work_trades.empty:
        work_trades["bucket_end_ns"] = ceil_epoch_ns(
            work_trades["timestamp"],
            bucket_ns,
        )
        in_window = work_trades["bucket_end_ns"].between(
            first_bucket,
            last_bucket,
        )
        window_trades = work_trades.loc[in_window].copy()
        trade_agg = window_trades.groupby("bucket_end_ns").agg(
            signed_volume=("signed_size", "sum"),
            absolute_volume=("size", "sum"),
            trade_records=("trade_id", "count"),
        )
    else:
        in_window = pd.Series(False, index=work_trades.index)
        window_trades = work_trades
        trade_agg = pd.DataFrame(
            columns=["signed_volume", "absolute_volume", "trade_records"]
        )

    panel = pd.DataFrame(
        {
            "bucket_end_ns": grid,
            "mid_price_end": mid_end.to_numpy(float),
            "bbo_updates": bbo_updates.to_numpy(int),
        }
    )
    panel["mid_price_start"] = panel["mid_price_end"].shift(1)
    panel["delta_mid_price"] = (
        panel["mid_price_end"] - panel["mid_price_start"]
    )
    for column in ["signed_volume", "absolute_volume", "trade_records"]:
        values = trade_agg[column] if column in trade_agg else pd.Series(
            dtype=float
        )
        panel[column] = values.reindex(grid, fill_value=0).to_numpy()

    clipped_start = panel["mid_price_start"].clip(
        LOG_ODDS_CLIP,
        1.0 - LOG_ODDS_CLIP,
    )
    clipped_end = panel["mid_price_end"].clip(
        LOG_ODDS_CLIP,
        1.0 - LOG_ODDS_CLIP,
    )
    panel["log_odds_start"] = np.log(
        clipped_start / (1.0 - clipped_start)
    )
    panel["log_odds_end"] = np.log(
        clipped_end / (1.0 - clipped_end)
    )
    panel["delta_log_odds"] = (
        panel["log_odds_end"] - panel["log_odds_start"]
    )
    panel["signed_volume_thousands"] = panel["signed_volume"] / 1000.0
    panel["bucket_end"] = pd.to_datetime(
        panel["bucket_end_ns"],
        unit="ns",
        utc=True,
    )
    panel["bucket_start"] = pd.to_datetime(
        panel["bucket_end_ns"] - bucket_ns,
        unit="ns",
        utc=True,
    )
    panel = panel.loc[panel["mid_price_start"].notna()].copy()
    panel["time_bucket_index"] = np.arange(1, len(panel) + 1)
    panel["minutes_from_first_regression_bucket"] = (
        panel["time_bucket_index"] - 1
    ) * bucket_seconds / 60.0
    panel["has_trade"] = panel["trade_records"].gt(0)

    for column, value in [
        ("bucket_seconds", bucket_seconds),
        ("game_id", key["game_id"]),
        ("selected_team", key["selected_team"]),
        ("platform", key["platform"]),
        ("instrument_id", key["instrument_id"]),
    ]:
        panel[column] = value

    keep_columns = [
        "bucket_seconds",
        "game_id",
        "selected_team",
        "platform",
        "instrument_id",
        "time_bucket_index",
        "minutes_from_first_regression_bucket",
        "bucket_start",
        "bucket_end",
        "mid_price_start",
        "mid_price_end",
        "delta_mid_price",
        "log_odds_start",
        "log_odds_end",
        "delta_log_odds",
        "signed_volume",
        "signed_volume_thousands",
        "absolute_volume",
        "trade_records",
        "bbo_updates",
        "has_trade",
    ]
    panel = panel[keep_columns]

    first_bbo = work_bbo["timestamp"].min()
    last_bbo = work_bbo["timestamp"].max()
    diagnostics = {
        **base,
        "first_bbo_timestamp": first_bbo,
        "last_bbo_timestamp": last_bbo,
        "bbo_span_minutes": (
            last_bbo - first_bbo
        ).total_seconds()
        / 60.0,
        "panel_rows": len(panel),
        "nonzero_flow_buckets": int(panel["signed_volume"].ne(0).sum()),
        "trade_rows_in_panel_window": len(window_trades),
        "trade_rows_outside_panel_window": (
            len(work_trades) - len(window_trades)
        ),
        "status": "ok" if len(panel) else "no_price_change_interval",
    }
    return panel, diagnostics


def build_panels(
    keys: pd.DataFrame,
    bbo: pd.DataFrame,
    trades: pd.DataFrame,
    bucket_seconds_values: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bbo_groups = {
        group_key: group.copy()
        for group_key, group in bbo.groupby(
            ["game_id", "platform", "instrument_id"],
            sort=False,
        )
    }
    trade_groups = {
        group_key: group.copy()
        for group_key, group in trades.groupby(
            ["game_id", "platform", "instrument_id"],
            sort=False,
        )
    }
    panels: list[pd.DataFrame] = []
    diagnostics: list[dict[str, object]] = []
    for key in keys.sort_values(["game_id", "platform"]).to_dict("records"):
        group_key = (
            key["game_id"],
            key["platform"],
            key["instrument_id"],
        )
        group_bbo = bbo_groups.get(group_key, pd.DataFrame())
        group_trades = trade_groups.get(group_key, pd.DataFrame())
        for bucket_seconds in bucket_seconds_values:
            panel, diagnostic = build_group_panel(
                pd.Series(key),
                group_bbo,
                group_trades,
                bucket_seconds,
            )
            diagnostics.append(diagnostic)
            if not panel.empty:
                panels.append(panel)
    if not panels:
        raise ValueError("No regression-panel rows could be constructed")
    return (
        pd.concat(panels, ignore_index=True).sort_values(
            [
                "bucket_seconds",
                "game_id",
                "platform",
                "time_bucket_index",
            ]
        ),
        pd.DataFrame(diagnostics).sort_values(
            ["bucket_seconds", "game_id", "platform"]
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build matched selected-team time buckets for Kyle-lambda "
            "regressions. Price changes use the last valid mid-price at each "
            "bucket end, carried forward through buckets without a quote "
            "update. Signed volume is summed inside the same interval."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=SCRIPT_PROJECT_ROOT,
    )
    parser.add_argument(
        "--bucket-seconds",
        type=int,
        nargs="+",
        default=list(DEFAULT_BUCKET_SECONDS),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    bucket_seconds_values = sorted(set(args.bucket_seconds))
    if not bucket_seconds_values or any(
        value <= 0 for value in bucket_seconds_values
    ):
        raise ValueError("Bucket sizes must be positive integer seconds")

    results_dir = root / "data/results"
    selection_path = results_dir / "selected_team_by_game.csv"
    panel_output = results_dir / "regression_input.csv"
    diagnostic_output = results_dir / "regression_panel_diagnostics.csv"
    bbo_paths = {
        "kalshi": (
            root
            / "data/processed/kalshi/filtered_bbo_trades/bbo_filtered.csv"
        ),
        "polymarket": (
            root
            / "data/processed/polymarket/filtered_bbo_trades/bbo_filtered.csv"
        ),
    }
    trade_paths = {
        "kalshi": (
            root
            / "data/processed/kalshi/filtered_bbo_trades/trades_filtered.csv"
        ),
        "polymarket": (
            root
            / "data/processed/polymarket/filtered_bbo_trades/trades_filtered.csv"
        ),
    }

    keys = load_selection(selection_path)
    print(f"Selected Kyle keys: {len(keys):,} rows across 64 games")
    print("Loading selected BBO observations...")
    bbo = load_selected_bbo(bbo_paths, keys)
    print(f"Valid selected BBO rows: {len(bbo):,}")
    print("Loading selected trades...")
    trades = load_selected_trades(trade_paths, keys)
    print(f"Valid selected trade rows: {len(trades):,}")
    print(
        "Building time buckets: "
        + ", ".join(f"{value}s" for value in bucket_seconds_values)
    )
    panel, diagnostics = build_panels(
        keys,
        bbo,
        trades,
        bucket_seconds_values,
    )

    if len(diagnostics) != len(keys) * len(bucket_seconds_values):
        raise ValueError("Regression diagnostics do not cover every key")
    if panel.duplicated(
        [
            "bucket_seconds",
            "game_id",
            "platform",
            "instrument_id",
            "bucket_end",
        ]
    ).any():
        raise ValueError("Regression panel contains duplicate bucket rows")

    results_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(panel_output, index=False)
    diagnostics.to_csv(diagnostic_output, index=False)

    print(f"Regression-panel rows: {len(panel):,}")
    print(
        "Panel rows by bucket size: "
        f"{panel.groupby('bucket_seconds').size().to_dict()}"
    )
    print(
        "Groups with usable price-change intervals: "
        f"{diagnostics.loc[diagnostics.panel_rows.gt(0)].groupby('bucket_seconds').size().to_dict()}"
    )
    print(
        "Diagnostic statuses: "
        f"{diagnostics.groupby(['bucket_seconds', 'status']).size().to_dict()}"
    )
    print(f"Saved: {panel_output}")
    print(f"Saved: {diagnostic_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
