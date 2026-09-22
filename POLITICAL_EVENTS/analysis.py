#!/usr/bin/env python3
"""Offline analysis pipeline for normalized political-market data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ANALYSIS_SCHEMA_VERSION = "2026-09-21.1"
DEFAULT_PRICE_IMPACT_SIZES = (100, 500, 1000)
PLATFORMS = ("kalshi", "polymarket")

PAIR_FIELDS = [
    "pair_id",
    "event_key",
    "family",
    "group_id",
    "canonical_outcome",
    "analysis_tier",
]


def require_columns(frame: pd.DataFrame, required: Iterable[str], source: Path) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{source} is missing required columns: {missing}")


def write_csv_atomic(frame: pd.DataFrame, output: Path, *, gzip: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    frame.to_csv(
        temporary,
        index=False,
        compression="gzip" if gzip else None,
    )
    temporary.replace(output)


def write_json_atomic(payload: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def parse_utc(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed


def numeric(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_market_map(
    event_root: Path,
    tier: str,
    max_markets: int = 0,
) -> pd.DataFrame:
    path = event_root / "data/matched_markets/market_map.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run the build-map stage before analysis."
        )
    market_map = pd.read_csv(path, dtype="string")
    require_columns(
        market_map,
        PAIR_FIELDS
        + [
            "comparison_start_utc",
            "treatment_time_utc",
            "comparison_end_utc",
            "full_window_covered",
        ],
        path,
    )
    if tier != "all":
        market_map = market_map.loc[market_map["analysis_tier"].eq(tier)].copy()
    if max_markets > 0:
        market_map = market_map.head(max_markets).copy()
    if market_map.empty:
        raise ValueError(f"No market-map rows selected for tier={tier}")
    if market_map["pair_id"].duplicated().any():
        raise ValueError("Market map contains duplicate pair_id values")
    covered = (
        market_map["full_window_covered"]
        .astype("string")
        .str.strip()
        .str.lower()
        .isin(["true", "1", "yes"])
    )
    if not covered.all():
        failed = market_map.loc[~covered, "pair_id"].tolist()
        raise ValueError(f"Selected pairs do not cover the full window: {failed}")
    return market_map.reset_index(drop=True)


def load_event_config(event_root: Path) -> dict:
    path = event_root / "event_config.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing event configuration: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_pair_membership(
    frame: pd.DataFrame,
    selected_pairs: set[str],
    source: Path,
) -> pd.DataFrame:
    frame["pair_id"] = frame["pair_id"].astype("string")
    unexpected = sorted(set(frame["pair_id"].dropna()) - selected_pairs)
    if unexpected:
        raise ValueError(f"{source} contains unexpected pair IDs: {unexpected}")
    return frame.loc[frame["pair_id"].isin(selected_pairs)].copy()


def load_trades(
    event_root: Path,
    market_map: pd.DataFrame,
    start: pd.Timestamp,
    treatment: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    selected_pairs = set(market_map["pair_id"])
    frames: list[pd.DataFrame] = []
    required = [
        "pair_id",
        "platform",
        "trade_id",
        "timestamp_ms",
        "observation_period",
        "canonical_yes_price",
        "size",
        "notional_usd",
    ]
    for platform in PLATFORMS:
        path = event_root / f"data/processed/{platform}/trades.csv.gz"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Run fetch-trades before analysis."
            )
        frame = pd.read_csv(
            path,
            compression="gzip",
            dtype={"pair_id": "string", "trade_id": "string"},
            low_memory=False,
        )
        require_columns(frame, required, path)
        frame = validate_pair_membership(frame, selected_pairs, path)
        if not frame.empty and not frame["platform"].eq(platform).all():
            raise ValueError(f"{path} contains an unexpected platform value")
        frames.append(frame)
    trades = pd.concat(frames, ignore_index=True)
    trades = numeric(
        trades,
        ["timestamp_ms", "canonical_yes_price", "size", "notional_usd"],
    )
    essential = ["timestamp_ms", "canonical_yes_price", "size", "notional_usd"]
    if trades[essential].isna().any().any():
        counts = trades[essential].isna().sum().to_dict()
        raise ValueError(f"Trade data contains missing essential values: {counts}")
    trades["timestamp_utc"] = pd.to_datetime(
        trades["timestamp_ms"], unit="ms", utc=True, errors="coerce"
    )
    if trades["timestamp_utc"].isna().any():
        raise ValueError("Trade data contains invalid timestamps")
    if not trades["canonical_yes_price"].between(0, 1).all():
        raise ValueError("Trade prices must lie in [0, 1]")
    if not trades["size"].gt(0).all():
        raise ValueError("Trade sizes must be strictly positive")
    if not trades["timestamp_utc"].between(start, end, inclusive="left").all():
        raise ValueError("Trade rows fall outside the configured study window")
    derived_period = np.where(trades["timestamp_utc"].lt(treatment), "pre", "post")
    if not np.array_equal(derived_period, trades["observation_period"].to_numpy()):
        raise ValueError("Trade pre/post labels disagree with the intervention time")
    duplicate_key = ["platform", "pair_id", "trade_id"]
    if trades.duplicated(duplicate_key).any():
        raise ValueError("Trade data contains duplicate platform/pair/trade IDs")
    trades["hour_utc"] = trades["timestamp_utc"].dt.floor("h")
    trades["price_x_size"] = trades["canonical_yes_price"] * trades["size"]
    return trades


def parse_book(value: object) -> list[dict[str, float]]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, list):
        decoded = value
    else:
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    levels: list[dict[str, float]] = []
    for level in decoded if isinstance(decoded, list) else []:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0:
            levels.append({"price": price, "size": size})
    return levels


def simulate_execution(
    levels: list[dict[str, float]],
    requested_size: float,
    *,
    ascending: bool,
) -> tuple[float, float]:
    """Return average execution price and filled share of requested size."""
    remaining = float(requested_size)
    notional = 0.0
    filled = 0.0
    for level in sorted(levels, key=lambda item: item["price"], reverse=not ascending):
        take = min(remaining, level["size"])
        notional += take * level["price"]
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    average = notional / filled if filled > 0 else np.nan
    return average, filled / requested_size if requested_size > 0 else np.nan


def add_price_impact_metrics(
    bbo: pd.DataFrame,
    sizes: tuple[int, ...],
) -> pd.DataFrame:
    if not sizes:
        return bbo
    bids = [parse_book(value) for value in bbo["bids_json"]]
    asks = [parse_book(value) for value in bbo["asks_json"]]
    mids = bbo["mid_price"].to_numpy(float)
    for requested in sizes:
        buy_impact: list[float] = []
        sell_impact: list[float] = []
        buy_fill: list[float] = []
        sell_fill: list[float] = []
        for index, mid in enumerate(mids):
            ask_average, ask_fill = simulate_execution(
                asks[index], requested, ascending=True
            )
            bid_average, bid_fill = simulate_execution(
                bids[index], requested, ascending=False
            )
            buy_fill.append(ask_fill)
            sell_fill.append(bid_fill)
            buy_impact.append(
                ask_average - mid if ask_fill >= 1 - 1e-9 else np.nan
            )
            sell_impact.append(
                mid - bid_average if bid_fill >= 1 - 1e-9 else np.nan
            )
        bbo[f"buy_price_impact_{requested}"] = buy_impact
        bbo[f"sell_price_impact_{requested}"] = sell_impact
        bbo[f"buy_fill_ratio_{requested}"] = buy_fill
        bbo[f"sell_fill_ratio_{requested}"] = sell_fill
        bbo[f"mean_price_impact_{requested}"] = bbo[
            [f"buy_price_impact_{requested}", f"sell_price_impact_{requested}"]
        ].mean(axis=1, skipna=False)
    return bbo


def load_bbo(
    event_root: Path,
    market_map: pd.DataFrame,
    start: pd.Timestamp,
    treatment: pd.Timestamp,
    end: pd.Timestamp,
    price_impact_sizes: tuple[int, ...],
) -> pd.DataFrame:
    selected_pairs = set(market_map["pair_id"])
    frames: list[pd.DataFrame] = []
    required = [
        "pair_id",
        "platform",
        "timestamp_ms",
        "observation_period",
        "canonical_yes_best_bid",
        "canonical_yes_best_ask",
        "mid_price",
        "spread",
        "best_bid_depth_contracts",
        "best_ask_depth_contracts",
        "full_bid_depth_contracts",
        "full_ask_depth_contracts",
        "full_bid_depth_notional",
        "full_ask_depth_notional",
        "bids_json",
        "asks_json",
    ]
    for platform in PLATFORMS:
        path = event_root / f"data/processed/{platform}/bbo.csv.gz"
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Run fetch-bbo before analysis.")
        frame = pd.read_csv(
            path,
            compression="gzip",
            dtype={"pair_id": "string", "instrument_id": "string"},
            low_memory=False,
        )
        require_columns(frame, required, path)
        frame = validate_pair_membership(frame, selected_pairs, path)
        if not frame.empty and not frame["platform"].eq(platform).all():
            raise ValueError(f"{path} contains an unexpected platform value")
        frames.append(frame)
    bbo = pd.concat(frames, ignore_index=True)
    numeric_columns = [
        "timestamp_ms",
        "canonical_yes_best_bid",
        "canonical_yes_best_ask",
        "mid_price",
        "spread",
        "best_bid_depth_contracts",
        "best_ask_depth_contracts",
        "full_bid_depth_contracts",
        "full_ask_depth_contracts",
        "full_bid_depth_notional",
        "full_ask_depth_notional",
    ]
    bbo = numeric(bbo, numeric_columns)
    if bbo["timestamp_ms"].isna().any():
        raise ValueError("BBO data contains invalid timestamps")
    bbo["timestamp_utc"] = pd.to_datetime(
        bbo["timestamp_ms"], unit="ms", utc=True, errors="coerce"
    )
    if not bbo["timestamp_utc"].between(start, end, inclusive="left").all():
        raise ValueError("BBO rows fall outside the configured study window")
    derived_period = np.where(bbo["timestamp_utc"].lt(treatment), "pre", "post")
    if not np.array_equal(derived_period, bbo["observation_period"].to_numpy()):
        raise ValueError("BBO pre/post labels disagree with the intervention time")
    bid = bbo["canonical_yes_best_bid"]
    ask = bbo["canonical_yes_best_ask"]
    bbo["usable_two_sided"] = (
        bid.notna()
        & ask.notna()
        & bid.gt(0)
        & ask.lt(1)
        & bid.le(ask)
    )
    bbo["mid_price"] = (bid + ask) / 2.0
    bbo["spread"] = ask - bid
    bid_full = bbo["full_bid_depth_contracts"]
    ask_full = bbo["full_ask_depth_contracts"]
    denominator = bid_full + ask_full
    bbo["full_depth_imbalance"] = np.where(
        denominator.gt(0), (bid_full - ask_full) / denominator, np.nan
    )
    bbo["hour_utc"] = bbo["timestamp_utc"].dt.floor("h")
    usable = bbo.loc[bbo["usable_two_sided"]].copy()
    return add_price_impact_metrics(usable, price_impact_sizes)


def aggregate_trades(trades: pd.DataFrame) -> pd.DataFrame:
    keys = ["pair_id", "platform", "hour_utc"]
    grouped = trades.groupby(keys, observed=True)
    hourly = grouped.agg(
        trade_records=("trade_id", "nunique"),
        trade_size=("size", "sum"),
        trade_notional_usd=("notional_usd", "sum"),
        trade_price_mean=("canonical_yes_price", "mean"),
        trade_price_std=("canonical_yes_price", "std"),
        trade_price_min=("canonical_yes_price", "min"),
        trade_price_max=("canonical_yes_price", "max"),
        trade_price_x_size=("price_x_size", "sum"),
    ).reset_index()
    hourly["trade_vwap_yes"] = np.where(
        hourly["trade_size"].gt(0),
        hourly["trade_price_x_size"] / hourly["trade_size"],
        np.nan,
    )
    return hourly.drop(columns="trade_price_x_size")


def aggregate_bbo(
    bbo: pd.DataFrame,
    sizes: tuple[int, ...],
) -> pd.DataFrame:
    keys = ["pair_id", "platform", "hour_utc"]
    mean_columns = [
        "spread",
        "mid_price",
        "best_bid_depth_contracts",
        "best_ask_depth_contracts",
        "full_bid_depth_contracts",
        "full_ask_depth_contracts",
        "full_bid_depth_notional",
        "full_ask_depth_notional",
        "full_depth_imbalance",
    ]
    for requested in sizes:
        mean_columns.extend(
            [
                f"buy_price_impact_{requested}",
                f"sell_price_impact_{requested}",
                f"mean_price_impact_{requested}",
                f"buy_fill_ratio_{requested}",
                f"sell_fill_ratio_{requested}",
            ]
        )
    if bbo.empty:
        return pd.DataFrame(
            columns=(
                keys
                + ["usable_bbo_records"]
                + [f"{column}_mean" for column in mean_columns]
                + ["last_quote_timestamp_utc", "mid_price_end", "spread_end"]
            )
        )
    grouped = bbo.groupby(keys, observed=True)
    hourly = grouped.size().rename("usable_bbo_records").reset_index()
    means = grouped[mean_columns].mean().add_suffix("_mean").reset_index()
    hourly = hourly.merge(means, on=keys, how="left", validate="one_to_one")
    last = (
        bbo.sort_values("timestamp_utc")
        .groupby(keys, observed=True)
        .tail(1)[keys + ["timestamp_utc", "mid_price", "spread"]]
        .rename(
            columns={
                "timestamp_utc": "last_quote_timestamp_utc",
                "mid_price": "mid_price_end",
                "spread": "spread_end",
            }
        )
    )
    return hourly.merge(last, on=keys, how="left", validate="one_to_one")


def build_hourly_panel(
    market_map: pd.DataFrame,
    trades: pd.DataFrame,
    bbo: pd.DataFrame,
    start: pd.Timestamp,
    treatment: pd.Timestamp,
    end: pd.Timestamp,
    sizes: tuple[int, ...],
) -> pd.DataFrame:
    hours = pd.DataFrame(
        {"hour_utc": pd.date_range(start=start, end=end, freq="h", inclusive="left")}
    )
    platforms = pd.DataFrame({"platform": list(PLATFORMS)})
    grid = (
        market_map[PAIR_FIELDS]
        .merge(platforms, how="cross")
        .merge(hours, how="cross")
    )
    trade_hourly = aggregate_trades(trades)
    bbo_hourly = aggregate_bbo(bbo, sizes)
    keys = ["pair_id", "platform", "hour_utc"]
    panel = grid.merge(trade_hourly, on=keys, how="left", validate="one_to_one")
    panel = panel.merge(bbo_hourly, on=keys, how="left", validate="one_to_one")
    count_columns = [
        "trade_records",
        "trade_size",
        "trade_notional_usd",
        "usable_bbo_records",
    ]
    for column in count_columns:
        if column not in panel:
            panel[column] = 0.0
        panel[column] = panel[column].fillna(0)
    panel["trade_records"] = panel["trade_records"].astype("int64")
    panel["usable_bbo_records"] = panel["usable_bbo_records"].astype("int64")
    panel["trade_hour_observed"] = panel["trade_records"].gt(0)
    panel["bbo_hour_observed"] = panel["usable_bbo_records"].gt(0)
    panel["observation_period"] = np.where(
        panel["hour_utc"].lt(treatment), "pre", "post"
    )
    panel["post"] = panel["observation_period"].eq("post").astype("int8")
    panel["is_polymarket"] = panel["platform"].eq("polymarket").astype("int8")
    panel["polymarket_post"] = panel["post"] * panel["is_polymarket"]
    panel["control_market"] = True
    panel["treatment_received"] = False
    panel["relative_hour"] = (
        (panel["hour_utc"] - treatment).dt.total_seconds() // 3600
    ).astype("int64")
    panel["pair_platform_id"] = panel["pair_id"] + "__" + panel["platform"]
    panel["calendar_hour_id"] = panel["hour_utc"].dt.strftime("%Y-%m-%dT%H:00Z")
    group_keys = ["pair_id", "platform"]
    panel = panel.sort_values(group_keys + ["hour_utc"]).reset_index(drop=True)
    for source, target in [
        ("mid_price_end", "mid_price_ffill"),
        ("spread_end", "spread_ffill"),
    ]:
        if source not in panel:
            panel[source] = np.nan
        panel[target] = panel.groupby(group_keys, observed=True)[source].ffill()
    panel["last_observed_quote_hour"] = panel["hour_utc"].where(
        panel["bbo_hour_observed"]
    )
    panel["last_observed_quote_hour"] = panel.groupby(
        group_keys, observed=True
    )["last_observed_quote_hour"].ffill()
    panel["quote_age_hours"] = (
        panel["hour_utc"] - panel["last_observed_quote_hour"]
    ).dt.total_seconds() / 3600.0
    panel["mid_price_change"] = panel.groupby(group_keys, observed=True)[
        "mid_price_ffill"
    ].diff()
    if len(panel) != len(market_map) * len(PLATFORMS) * len(hours):
        raise ValueError("Hourly panel is not balanced across pairs and platforms")
    if panel.duplicated(keys).any():
        raise ValueError("Hourly panel contains duplicate pair/platform/hour rows")
    return panel


def summarize_panel(panel: pd.DataFrame, sizes: tuple[int, ...]) -> pd.DataFrame:
    keys = PAIR_FIELDS + ["platform", "observation_period"]
    aggregations: dict[str, tuple[str, str]] = {
        "expected_hours": ("hour_utc", "size"),
        "trade_records": ("trade_records", "sum"),
        "trade_size": ("trade_size", "sum"),
        "trade_notional_usd": ("trade_notional_usd", "sum"),
        "active_trade_hours": ("trade_hour_observed", "sum"),
        "bbo_hour_bins": ("bbo_hour_observed", "sum"),
        "usable_bbo_records": ("usable_bbo_records", "sum"),
        "quote_available_hours": ("mid_price_ffill", "count"),
        "mean_quote_age_hours": ("quote_age_hours", "mean"),
        "mean_spread": ("spread_mean", "mean"),
        "median_spread": ("spread_mean", "median"),
        "mean_best_bid_depth": ("best_bid_depth_contracts_mean", "mean"),
        "mean_best_ask_depth": ("best_ask_depth_contracts_mean", "mean"),
        "mean_full_bid_depth": ("full_bid_depth_contracts_mean", "mean"),
        "mean_full_ask_depth": ("full_ask_depth_contracts_mean", "mean"),
        "mean_full_bid_notional": ("full_bid_depth_notional_mean", "mean"),
        "mean_full_ask_notional": ("full_ask_depth_notional_mean", "mean"),
        "hourly_mid_price_volatility": ("mid_price_change", "std"),
    }
    for requested in sizes:
        column = f"mean_price_impact_{requested}_mean"
        aggregations[f"mean_price_impact_{requested}"] = (column, "mean")
        aggregations[f"mean_buy_fill_ratio_{requested}"] = (
            f"buy_fill_ratio_{requested}_mean",
            "mean",
        )
        aggregations[f"mean_sell_fill_ratio_{requested}"] = (
            f"sell_fill_ratio_{requested}_mean",
            "mean",
        )
    summary = (
        panel.groupby(keys, observed=True)
        .agg(**aggregations)
        .reset_index()
    )
    summary["trade_hour_coverage_pct"] = (
        summary["active_trade_hours"] / summary["expected_hours"] * 100
    )
    summary["bbo_hour_coverage_pct"] = (
        summary["bbo_hour_bins"] / summary["expected_hours"] * 100
    )
    summary["quote_available_coverage_pct"] = (
        summary["quote_available_hours"] / summary["expected_hours"] * 100
    )
    return summary


def paired_platform_differences(summary: pd.DataFrame) -> pd.DataFrame:
    identifiers = [
        "pair_id",
        "event_key",
        "family",
        "group_id",
        "canonical_outcome",
        "analysis_tier",
        "observation_period",
    ]
    metrics = [
        "trade_records",
        "trade_size",
        "trade_notional_usd",
        "bbo_hour_coverage_pct",
        "mean_spread",
        "mean_best_bid_depth",
        "mean_best_ask_depth",
        "mean_full_bid_depth",
        "mean_full_ask_depth",
        "hourly_mid_price_volatility",
    ] + [
        column for column in summary.columns if column.startswith("mean_price_impact_")
    ]
    wide = summary.pivot(index=identifiers, columns="platform", values=metrics)
    wide.columns = [f"{metric}_{platform}" for metric, platform in wide.columns]
    wide = wide.reset_index()
    for metric in metrics:
        kalshi = f"{metric}_kalshi"
        poly = f"{metric}_polymarket"
        if kalshi not in wide or poly not in wide:
            continue
        wide[f"{metric}_polymarket_minus_kalshi"] = wide[poly] - wide[kalshi]
        wide[f"{metric}_polymarket_to_kalshi_ratio"] = np.where(
            wide[kalshi].ne(0), wide[poly] / wide[kalshi], np.nan
        )
    return wide


def pre_post_changes(summary: pd.DataFrame) -> pd.DataFrame:
    identifiers = PAIR_FIELDS + ["platform"]
    metrics = [
        "trade_records",
        "trade_size",
        "trade_notional_usd",
        "trade_hour_coverage_pct",
        "bbo_hour_coverage_pct",
        "mean_spread",
        "mean_best_bid_depth",
        "mean_best_ask_depth",
        "mean_full_bid_depth",
        "mean_full_ask_depth",
        "hourly_mid_price_volatility",
    ] + [
        column for column in summary.columns if column.startswith("mean_price_impact_")
    ]
    wide = summary.pivot(index=identifiers, columns="observation_period", values=metrics)
    wide.columns = [f"{metric}_{period}" for metric, period in wide.columns]
    wide = wide.reset_index()
    for metric in metrics:
        pre = f"{metric}_pre"
        post = f"{metric}_post"
        if pre not in wide or post not in wide:
            continue
        wide[f"{metric}_post_minus_pre"] = wide[post] - wide[pre]
        wide[f"{metric}_change_pct"] = np.where(
            wide[pre].ne(0), (wide[post] - wide[pre]) / wide[pre] * 100, np.nan
        )
    return wide


def quality_diagnostics(panel: pd.DataFrame) -> pd.DataFrame:
    keys = PAIR_FIELDS + ["platform"]
    quality = (
        panel.groupby(keys, observed=True)
        .agg(
            panel_hours=("hour_utc", "size"),
            trade_records=("trade_records", "sum"),
            active_trade_hours=("trade_hour_observed", "sum"),
            bbo_hour_bins=("bbo_hour_observed", "sum"),
            usable_bbo_records=("usable_bbo_records", "sum"),
            quote_available_hours=("mid_price_ffill", "count"),
            maximum_quote_age_hours=("quote_age_hours", "max"),
        )
        .reset_index()
    )
    quality["has_trades"] = quality["trade_records"].gt(0)
    quality["has_two_sided_bbo"] = quality["usable_bbo_records"].gt(0)
    quality["complete_336_hour_grid"] = quality["panel_hours"].eq(336)
    return quality


def build_analysis_outputs(
    event_root: Path,
    *,
    tier: str = "all",
    max_markets: int = 0,
    price_impact_sizes: tuple[int, ...] = DEFAULT_PRICE_IMPACT_SIZES,
    run_log=None,
) -> dict[str, int]:
    config = load_event_config(event_root)
    window = config["study_window"]
    start = parse_utc(window["start_utc"])
    treatment = parse_utc(window["treatment_utc"])
    end = parse_utc(window["end_utc"])
    market_map = load_market_map(event_root, tier, max_markets)
    if run_log:
        run_log.info(f"Loading normalized trades for {len(market_map)} pairs")
    trades = load_trades(event_root, market_map, start, treatment, end)
    if run_log:
        run_log.info(f"Loaded {len(trades)} normalized trade rows")
        run_log.info("Loading and validating normalized BBO snapshots")
    bbo = load_bbo(
        event_root,
        market_map,
        start,
        treatment,
        end,
        price_impact_sizes,
    )
    if run_log:
        run_log.info(f"Loaded {len(bbo)} usable two-sided BBO rows")
    panel = build_hourly_panel(
        market_map,
        trades,
        bbo,
        start,
        treatment,
        end,
        price_impact_sizes,
    )
    summary = summarize_panel(panel, price_impact_sizes)
    differences = paired_platform_differences(summary)
    changes = pre_post_changes(summary)
    quality = quality_diagnostics(panel)

    results = event_root / "data/results"
    audits = event_root / "data/audits"
    outputs = {
        "hourly_panel": results / "hourly_market_panel.csv.gz",
        "summary": results / "metrics_by_pair_platform_period.csv",
        "platform_differences": results / "platform_differences_by_pair_period.csv",
        "pre_post_changes": results / "pre_post_changes_by_pair_platform.csv",
        "quality": audits / "analysis_quality_by_pair_platform.csv",
        "manifest": results / "analysis_manifest.json",
    }
    write_csv_atomic(panel, outputs["hourly_panel"], gzip=True)
    write_csv_atomic(summary, outputs["summary"])
    write_csv_atomic(differences, outputs["platform_differences"])
    write_csv_atomic(changes, outputs["pre_post_changes"])
    write_csv_atomic(quality, outputs["quality"])
    write_json_atomic(
        {
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_key": config["event_key"],
            "tier": tier,
            "max_markets": max_markets,
            "price_impact_sizes": list(price_impact_sizes),
            "study_window": window,
            "pairs": len(market_map),
            "input_rows": {"trades": len(trades), "usable_bbo": len(bbo)},
            "output_rows": {
                "hourly_panel": len(panel),
                "summary": len(summary),
                "platform_differences": len(differences),
                "pre_post_changes": len(changes),
                "quality": len(quality),
            },
            "outputs": {key: str(value) for key, value in outputs.items() if key != "manifest"},
        },
        outputs["manifest"],
    )
    if run_log:
        run_log.info(
            f"Analysis complete panel_rows={len(panel)} summary_rows={len(summary)}"
        )
    return {
        "pairs": len(market_map),
        "trades": len(trades),
        "usable_bbo": len(bbo),
        "panel": len(panel),
        "summary": len(summary),
    }


def combine_event_outputs(political_root: Path) -> dict[str, int]:
    event_directories = [
        "BRAZIL_PRESIDENTIAL_ELECTION",
        "ICELAND_EU_REFERENDUM",
        "MASSACHUSETTS_DEMOCRATIC_PRIMARIES",
    ]
    panels: list[pd.DataFrame] = []
    summaries: list[pd.DataFrame] = []
    missing: list[str] = []
    for directory in event_directories:
        event_root = political_root / directory
        panel_path = event_root / "data/results/hourly_market_panel.csv.gz"
        summary_path = event_root / "data/results/metrics_by_pair_platform_period.csv"
        if not panel_path.exists() or not summary_path.exists():
            missing.append(directory)
            continue
        panels.append(pd.read_csv(panel_path, compression="gzip", low_memory=False))
        summaries.append(pd.read_csv(summary_path, low_memory=False))
    if missing:
        raise FileNotFoundError(
            "Run the analyze stage for every event before combining. Missing: "
            + ", ".join(missing)
        )
    panel = pd.concat(panels, ignore_index=True)
    summary = pd.concat(summaries, ignore_index=True)
    key = ["event_key", "pair_id", "platform", "hour_utc"]
    if panel.duplicated(key).any():
        raise ValueError("Combined political panel contains duplicate keys")
    results = political_root / "data/results"
    panel_output = results / "political_hourly_panel.csv.gz"
    summary_output = results / "political_metrics_by_pair_platform_period.csv"
    manifest_output = results / "combined_analysis_manifest.json"
    write_csv_atomic(panel, panel_output, gzip=True)
    write_csv_atomic(summary, summary_output)
    write_json_atomic(
        {
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "events": event_directories,
            "event_count": len(event_directories),
            "pair_count": int(panel["pair_id"].nunique()),
            "panel_rows": len(panel),
            "summary_rows": len(summary),
            "outputs": {
                "hourly_panel": str(panel_output),
                "summary": str(summary_output),
            },
        },
        manifest_output,
    )
    return {
        "events": len(event_directories),
        "pairs": int(panel["pair_id"].nunique()),
        "panel": len(panel),
        "summary": len(summary),
    }
