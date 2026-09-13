from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    label: str
    group: str
    sample: str
    unit: str
    preferred_direction: str


METRICS = (
    MetricSpec(
        "avg_spread",
        "Average bid-ask spread",
        "Spread",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "median_spread",
        "Median bid-ask spread",
        "Spread",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_best_bid_depth",
        "Average best-level bid depth",
        "Best-level depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "median_best_bid_depth",
        "Median best-level bid depth",
        "Best-level depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "avg_best_ask_depth",
        "Average best-level ask depth",
        "Best-level depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "median_best_ask_depth",
        "Median best-level ask depth",
        "Best-level depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "avg_full_bid_depth",
        "Average full-book bid-side depth",
        "Full-book depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "median_full_bid_depth",
        "Median full-book bid-side depth",
        "Full-book depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "avg_full_ask_depth",
        "Average full-book ask-side depth",
        "Full-book depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "median_full_ask_depth",
        "Median full-book ask-side depth",
        "Full-book depth",
        "bbo_joint",
        "contracts",
        "higher",
    ),
    MetricSpec(
        "avg_best_bid_depth_notional",
        "Average best-level bid notional",
        "Best-level notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "median_best_bid_depth_notional",
        "Median best-level bid notional",
        "Best-level notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "avg_best_ask_depth_notional",
        "Average best-level ask notional",
        "Best-level notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "median_best_ask_depth_notional",
        "Median best-level ask notional",
        "Best-level notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "avg_full_bid_depth_notional",
        "Average full-book bid-side notional",
        "Full-book notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "median_full_bid_depth_notional",
        "Median full-book bid-side notional",
        "Full-book notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "avg_full_ask_depth_notional",
        "Average full-book ask-side notional",
        "Full-book notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "median_full_ask_depth_notional",
        "Median full-book ask-side notional",
        "Full-book notional depth",
        "bbo_joint",
        "notional",
        "higher",
    ),
    MetricSpec(
        "mid_price_volatility",
        "Mid-price volatility",
        "Volatility",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_buy_impact_100",
        "Average simulated buy impact (100 contracts)",
        "Simulated buy impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_buy_impact_500",
        "Average simulated buy impact (500 contracts)",
        "Simulated buy impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_buy_impact_1000",
        "Average simulated buy impact (1,000 contracts)",
        "Simulated buy impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_sell_impact_100",
        "Average simulated sell impact (100 contracts)",
        "Simulated sell impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_sell_impact_500",
        "Average simulated sell impact (500 contracts)",
        "Simulated sell impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "avg_sell_impact_1000",
        "Average simulated sell impact (1,000 contracts)",
        "Simulated sell impact",
        "bbo_joint",
        "probability",
        "lower",
    ),
    MetricSpec(
        "fill_rate_buy_100",
        "Buy fill rate (100 contracts)",
        "Simulated buy fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "fill_rate_buy_500",
        "Buy fill rate (500 contracts)",
        "Simulated buy fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "fill_rate_buy_1000",
        "Buy fill rate (1,000 contracts)",
        "Simulated buy fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "fill_rate_sell_100",
        "Sell fill rate (100 contracts)",
        "Simulated sell fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "fill_rate_sell_500",
        "Sell fill rate (500 contracts)",
        "Simulated sell fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "fill_rate_sell_1000",
        "Sell fill rate (1,000 contracts)",
        "Simulated sell fill rate",
        "bbo_joint",
        "proportion",
        "higher",
    ),
    MetricSpec(
        "num_trades",
        "Number of trade records/fills",
        "Trading activity",
        "trade",
        "count",
        "descriptive",
    ),
    MetricSpec(
        "total_volume",
        "Total contract volume",
        "Trading activity",
        "trade",
        "contracts",
        "descriptive",
    ),
    MetricSpec(
        "avg_trade_size",
        "Average trade size",
        "Trade size",
        "trade",
        "contracts",
        "descriptive",
    ),
    MetricSpec(
        "median_trade_size",
        "Median trade size",
        "Trade size",
        "trade",
        "contracts",
        "descriptive",
    ),
    MetricSpec(
        "total_notional",
        "Total selected-outcome traded notional",
        "Trading activity",
        "trade",
        "notional",
        "descriptive",
    ),
    MetricSpec(
        "trade_price_volatility",
        "Trade-price volatility",
        "Volatility",
        "trade",
        "probability",
        "lower",
    ),
)


EXCLUDED_METRICS: tuple[MetricSpec, ...] = ()


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def load_selected_metrics(path: Path) -> pd.DataFrame:
    require_file(path)
    frame = pd.read_csv(
        path,
        dtype={
            "game_id": "string",
            "platform": "string",
            "selected_team": "string",
            "instrument_id": "string",
        },
    )
    required = {
        "game_id",
        "platform",
        "selected_team",
        "instrument_id",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    if frame.duplicated(["game_id", "platform"]).any():
        raise ValueError(f"{path} contains duplicate game-platform rows")
    if set(frame["platform"].dropna()) != {"kalshi", "polymarket"}:
        raise ValueError(f"{path} must contain both platforms")
    return frame


def paired_values(
    frame: pd.DataFrame,
    spec: MetricSpec,
) -> pd.DataFrame:
    if spec.metric not in frame.columns:
        raise ValueError(f"Missing metric column: {spec.metric}")

    subset = frame[
        ["game_id", "selected_team", "platform", spec.metric]
    ].copy()
    subset[spec.metric] = pd.to_numeric(subset[spec.metric], errors="coerce")
    pivot = subset.pivot(
        index=["game_id", "selected_team"],
        columns="platform",
        values=spec.metric,
    ).reset_index()
    for platform in ["kalshi", "polymarket"]:
        if platform not in pivot.columns:
            pivot[platform] = np.nan
    pivot = pivot.dropna(subset=["kalshi", "polymarket"]).copy()
    pivot["kalshi_minus_polymarket"] = (
        pivot["kalshi"] - pivot["polymarket"]
    )
    pivot["kalshi_to_polymarket_ratio"] = np.where(
        pivot["polymarket"].ne(0),
        pivot["kalshi"] / pivot["polymarket"],
        np.nan,
    )
    pivot.insert(0, "sample", spec.sample)
    pivot.insert(1, "metric_group", spec.group)
    pivot.insert(2, "metric", spec.metric)
    pivot.insert(3, "metric_label", spec.label)
    pivot.insert(4, "unit", spec.unit)
    pivot.insert(5, "preferred_direction", spec.preferred_direction)
    return pivot[
        [
            "sample",
            "metric_group",
            "metric",
            "metric_label",
            "unit",
            "preferred_direction",
            "game_id",
            "selected_team",
            "kalshi",
            "polymarket",
            "kalshi_minus_polymarket",
            "kalshi_to_polymarket_ratio",
        ]
    ]


def summarize_pairs(pairs: pd.DataFrame, spec: MetricSpec) -> dict[str, object]:
    kalshi = pairs["kalshi"]
    polymarket = pairs["polymarket"]
    difference = pairs["kalshi_minus_polymarket"]
    ratio = pairs["kalshi_to_polymarket_ratio"].replace(
        [np.inf, -np.inf], np.nan
    )
    return {
        "sample": spec.sample,
        "metric_group": spec.group,
        "metric": spec.metric,
        "metric_label": spec.label,
        "unit": spec.unit,
        "preferred_direction": spec.preferred_direction,
        "n_paired_games": len(pairs),
        "kalshi_mean": kalshi.mean(),
        "kalshi_median": kalshi.median(),
        "kalshi_std": kalshi.std(ddof=1),
        "kalshi_q25": kalshi.quantile(0.25),
        "kalshi_q75": kalshi.quantile(0.75),
        "polymarket_mean": polymarket.mean(),
        "polymarket_median": polymarket.median(),
        "polymarket_std": polymarket.std(ddof=1),
        "polymarket_q25": polymarket.quantile(0.25),
        "polymarket_q75": polymarket.quantile(0.75),
        "mean_kalshi_minus_polymarket": difference.mean(),
        "median_kalshi_minus_polymarket": difference.median(),
        "std_kalshi_minus_polymarket": difference.std(ddof=1),
        "share_games_kalshi_greater": difference.gt(0).mean(),
        "median_kalshi_to_polymarket_ratio": ratio.median(),
    }


def availability_row(
    frame: pd.DataFrame,
    spec: MetricSpec,
    included: bool,
) -> dict[str, object]:
    if spec.metric not in frame.columns:
        return {
            "sample": spec.sample,
            "metric_group": spec.group,
            "metric": spec.metric,
            "metric_label": spec.label,
            "kalshi_nonmissing": 0,
            "polymarket_nonmissing": 0,
            "paired_nonmissing": 0,
            "included": False,
            "reason": "Metric column is missing",
        }
    subset = frame[["game_id", "platform", spec.metric]].copy()
    subset[spec.metric] = pd.to_numeric(subset[spec.metric], errors="coerce")
    counts = subset.groupby("platform")[spec.metric].count()
    pivot = subset.pivot(
        index="game_id", columns="platform", values=spec.metric
    )
    paired = int(
        pivot.get("kalshi", pd.Series(index=pivot.index, dtype=float))
        .notna()
        .mul(
            pivot.get(
                "polymarket", pd.Series(index=pivot.index, dtype=float)
            ).notna()
        )
        .sum()
    )
    if included:
        reason = "Included in paired comparison"
    else:
        reason = (
            "Excluded: Kalshi sell-impact values are unavailable for "
            "nearly all selected games"
        )
    return {
        "sample": spec.sample,
        "metric_group": spec.group,
        "metric": spec.metric,
        "metric_label": spec.label,
        "kalshi_nonmissing": int(counts.get("kalshi", 0)),
        "polymarket_nonmissing": int(counts.get("polymarket", 0)),
        "paired_nonmissing": paired,
        "included": included,
        "reason": reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=SCRIPT_PROJECT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    results_dir = root / "data/results"
    all_selected_path = results_dir / "summary_metrics_selected_team.csv"
    bbo_selected_path = (
        results_dir / "summary_metrics_selected_team_bbo_joint.csv"
    )
    paired_output = (
        results_dir / "selected_team_paired_metric_values.csv"
    )
    summary_output = (
        results_dir / "selected_team_platform_comparison.csv"
    )
    availability_output = (
        results_dir / "selected_team_metric_availability.csv"
    )

    all_selected = load_selected_metrics(all_selected_path)
    bbo_selected = load_selected_metrics(bbo_selected_path)
    if all_selected["game_id"].nunique() != 67 or len(all_selected) != 134:
        raise ValueError("Expected 67 games and 134 rows in the trade sample")
    if bbo_selected["game_id"].nunique() != 64 or len(bbo_selected) != 128:
        raise ValueError("Expected 64 games and 128 rows in the BBO sample")

    frames = {
        "trade": all_selected,
        "bbo_joint": bbo_selected,
    }
    pair_frames = []
    summary_records = []
    availability_records = []
    for spec in METRICS:
        source = frames[spec.sample]
        pairs = paired_values(source, spec)
        if pairs.empty:
            raise ValueError(f"No paired observations for {spec.metric}")
        pair_frames.append(pairs)
        summary_records.append(summarize_pairs(pairs, spec))
        availability_records.append(
            availability_row(source, spec, included=True)
        )
    for spec in EXCLUDED_METRICS:
        availability_records.append(
            availability_row(frames[spec.sample], spec, included=False)
        )

    paired = pd.concat(pair_frames, ignore_index=True)
    summary = pd.DataFrame(summary_records)
    availability = pd.DataFrame(availability_records)

    expected_trade_pairs = 67 * sum(
        metric.sample == "trade" for metric in METRICS
    )
    bbo_metric_count = sum(
        metric.sample == "bbo_joint" for metric in METRICS
    )
    bbo_expected_max = 64 * bbo_metric_count
    if len(paired.loc[paired["sample"].eq("trade")]) != expected_trade_pairs:
        raise ValueError("Trade metrics do not have complete 67-game pairing")
    if len(paired.loc[paired["sample"].eq("bbo_joint")]) > bbo_expected_max:
        raise ValueError("BBO paired-row count exceeds its expected maximum")
    bbo_counts = summary.loc[
        summary["sample"].eq("bbo_joint"),
        ["metric", "n_paired_games"],
    ].set_index("metric")["n_paired_games"]
    expected_bbo_counts = pd.Series(
        {
            spec.metric: (
                62 if spec.metric == "mid_price_volatility" else 64
            )
            for spec in METRICS
            if spec.sample == "bbo_joint"
        },
        dtype="int64",
    )
    if not bbo_counts.astype("int64").equals(expected_bbo_counts):
        comparison = pd.concat(
            [
                bbo_counts.rename("actual"),
                expected_bbo_counts.rename("expected"),
            ],
            axis=1,
        )
        raise ValueError(
            "Corrected BBO metrics do not have the expected paired-game "
            f"coverage:\n{comparison.to_string()}"
        )
    if not availability["included"].all():
        raise ValueError("Corrected comparison unexpectedly excludes a metric")

    paired.to_csv(paired_output, index=False)
    summary.to_csv(summary_output, index=False)
    availability.to_csv(availability_output, index=False)

    print(f"Paired metric rows: {len(paired):,}")
    print(f"Summary metrics: {len(summary):,}")
    print(
        "Paired-game counts by metric: "
        f"{summary['n_paired_games'].value_counts().sort_index().to_dict()}"
    )
    print(
        "Trade metrics with 67 pairs: "
        f"{int((summary.loc[summary['sample'].eq('trade'), 'n_paired_games'] == 67).sum())}"
    )
    print(
        "BBO metrics with 64 pairs: "
        f"{int((summary.loc[summary['sample'].eq('bbo_joint'), 'n_paired_games'] == 64).sum())}"
    )
    print(f"Saved: {paired_output}")
    print(f"Saved: {summary_output}")
    print(f"Saved: {availability_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
