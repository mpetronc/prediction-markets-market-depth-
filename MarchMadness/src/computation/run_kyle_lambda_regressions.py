from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_OBSERVATIONS = 10
PRIMARY_BUCKET_SECONDS = 60


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def continued_fraction_beta(a: float, b: float, x: float) -> float:
    max_iterations = 200
    epsilon = 3e-14
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = (
            iteration
            * (b - iteration)
            * x
            / ((qam + m2) * (a + m2))
        )
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c

        aa = -(
            (a + iteration)
            * (qab + iteration)
            * x
            / ((a + m2) * (qap + m2))
        )
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_term = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    front = math.exp(log_term)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * continued_fraction_beta(a, b, x) / a
    return 1.0 - (
        front * continued_fraction_beta(b, a, 1.0 - x) / b
    )


def student_t_two_sided_p(t_stat: float, degrees_freedom: int) -> float:
    if degrees_freedom <= 0 or not math.isfinite(t_stat):
        return np.nan
    x = degrees_freedom / (degrees_freedom + t_stat * t_stat)
    return min(
        1.0,
        regularized_incomplete_beta(
            degrees_freedom / 2.0,
            0.5,
            x,
        ),
    )


def student_t_cdf(t_value: float, degrees_freedom: int) -> float:
    if t_value == 0:
        return 0.5
    x = degrees_freedom / (
        degrees_freedom + t_value * t_value
    )
    tail_twice = regularized_incomplete_beta(
        degrees_freedom / 2.0,
        0.5,
        x,
    )
    if t_value > 0:
        return 1.0 - tail_twice / 2.0
    return tail_twice / 2.0


def student_t_critical_975(degrees_freedom: int) -> float:
    if degrees_freedom <= 0:
        return np.nan
    low, high = 0.0, 20.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        if student_t_cdf(midpoint, degrees_freedom) < 0.975:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def newey_west_lag(n_observations: int) -> int:
    if n_observations <= 1:
        return 0
    return min(
        n_observations - 1,
        int(math.floor(4.0 * (n_observations / 100.0) ** (2.0 / 9.0))),
    )


def ols_with_hac(
    y: np.ndarray,
    x: np.ndarray,
    include_intercept: bool,
) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    finite = np.isfinite(y) & np.isfinite(x)
    y = y[finite]
    x = x[finite]
    n = len(y)
    design = (
        np.column_stack([np.ones(n), x])
        if include_intercept
        else x.reshape(-1, 1)
    )
    k = design.shape[1]
    if n <= k:
        raise ValueError("Not enough observations for OLS")
    xtx = design.T @ design
    if np.linalg.matrix_rank(xtx) < k:
        raise ValueError("Regression design matrix is singular")
    xtx_inverse = np.linalg.inv(xtx)
    coefficients = xtx_inverse @ design.T @ y
    residuals = y - design @ coefficients

    lag = newey_west_lag(n)
    score = design * residuals[:, None]
    meat = score.T @ score
    for lag_index in range(1, lag + 1):
        weight = 1.0 - lag_index / (lag + 1.0)
        cross = score[lag_index:].T @ score[:-lag_index]
        meat += weight * (cross + cross.T)
    covariance_hac = xtx_inverse @ meat @ xtx_inverse
    covariance_hac *= n / (n - k)
    hac_standard_errors = np.sqrt(
        np.maximum(np.diag(covariance_hac), 0.0)
    )

    sigma_squared = float(residuals @ residuals) / (n - k)
    classical_covariance = sigma_squared * xtx_inverse
    classical_standard_errors = np.sqrt(
        np.maximum(np.diag(classical_covariance), 0.0)
    )

    slope_index = 1 if include_intercept else 0
    slope = float(coefficients[slope_index])
    slope_se_hac = float(hac_standard_errors[slope_index])
    slope_se_classical = float(classical_standard_errors[slope_index])
    t_hac = (
        slope / slope_se_hac if slope_se_hac > 0 else np.nan
    )
    df = n - k
    critical = student_t_critical_975(df)
    if include_intercept:
        total_sum_squares = float(
            ((y - y.mean()) ** 2).sum()
        )
    else:
        total_sum_squares = float((y**2).sum())
    residual_sum_squares = float((residuals**2).sum())
    r_squared = (
        1.0 - residual_sum_squares / total_sum_squares
        if total_sum_squares > 0
        else np.nan
    )
    return {
        "n_observations": n,
        "degrees_freedom": df,
        "hac_lags": lag,
        "alpha": (
            float(coefficients[0]) if include_intercept else 0.0
        ),
        "lambda_per_1000_contracts": slope,
        "lambda_se_hac": slope_se_hac,
        "lambda_se_classical": slope_se_classical,
        "lambda_t_hac": t_hac,
        "lambda_p_value_hac": student_t_two_sided_p(t_hac, df),
        "lambda_ci95_low_hac": slope - critical * slope_se_hac,
        "lambda_ci95_high_hac": slope + critical * slope_se_hac,
        "r_squared": r_squared,
        "residual_std": math.sqrt(sigma_squared),
    }


def estimate_group(
    panel: pd.DataFrame,
    price_transform: str,
    include_intercept: bool,
    min_observations: int,
) -> dict[str, object]:
    outcome_column = {
        "probability": "delta_mid_price",
        "log_odds": "delta_log_odds",
    }[price_transform]
    clean = panel[
        [outcome_column, "signed_volume_thousands"]
    ].dropna()
    x = clean["signed_volume_thousands"].to_numpy(float)
    y = clean[outcome_column].to_numpy(float)
    base = {
        "n_observations": len(clean),
        "nonzero_flow_buckets": int(np.count_nonzero(x)),
        "price_change_buckets": int(np.count_nonzero(y)),
        "signed_volume_std_thousands": (
            float(np.std(x, ddof=1)) if len(x) > 1 else np.nan
        ),
        "status": "ok",
        "reason": "",
    }
    if len(clean) < min_observations:
        return {
            **base,
            "status": "insufficient_observations",
            "reason": (
                f"Fewer than {min_observations} time-bucket observations"
            ),
        }
    if np.count_nonzero(x) == 0:
        return {
            **base,
            "status": "no_signed_flow_variation",
            "reason": "All time buckets have zero signed volume",
        }
    if include_intercept and np.isclose(np.var(x), 0.0):
        return {
            **base,
            "status": "no_signed_flow_variation",
            "reason": "Signed volume has no variation",
        }
    try:
        estimates = ols_with_hac(y, x, include_intercept)
    except ValueError as exc:
        return {
            **base,
            "status": "estimation_failed",
            "reason": str(exc),
        }
    return {**base, **estimates}


def estimate_lambdas(
    panel: pd.DataFrame,
    diagnostics: pd.DataFrame,
    min_observations: int,
) -> pd.DataFrame:
    group_columns = [
        "bucket_seconds",
        "game_id",
        "selected_team",
        "platform",
        "instrument_id",
    ]
    panel_groups = {
        key: group.sort_values("time_bucket_index").copy()
        for key, group in panel.groupby(group_columns, sort=False)
    }
    records: list[dict[str, object]] = []
    for diagnostic in diagnostics.itertuples(index=False):
        key = tuple(getattr(diagnostic, column) for column in group_columns)
        group = panel_groups.get(key, pd.DataFrame())
        for price_transform in ["probability", "log_odds"]:
            for include_intercept in [False, True]:
                common = {
                    column: getattr(diagnostic, column)
                    for column in group_columns
                }
                common.update(
                    {
                        "price_transform": price_transform,
                        "include_intercept": include_intercept,
                        "specification": (
                            "with_intercept"
                            if include_intercept
                            else "no_intercept"
                        ),
                        "is_primary_specification": (
                            diagnostic.bucket_seconds
                            == PRIMARY_BUCKET_SECONDS
                            and price_transform == "probability"
                            and not include_intercept
                        ),
                        "bbo_rows": diagnostic.bbo_rows,
                        "trade_rows": diagnostic.trade_rows,
                        "bbo_span_minutes": diagnostic.bbo_span_minutes,
                        "panel_rows_available": diagnostic.panel_rows,
                    }
                )
                if group.empty:
                    estimate = {
                        "n_observations": 0,
                        "nonzero_flow_buckets": 0,
                        "price_change_buckets": 0,
                        "signed_volume_std_thousands": np.nan,
                        "status": "no_regression_panel",
                        "reason": str(diagnostic.status),
                    }
                else:
                    estimate = estimate_group(
                        group,
                        price_transform,
                        include_intercept,
                        min_observations,
                    )
                records.append({**common, **estimate})
    estimates = pd.DataFrame(records)
    numeric_outputs = [
        "alpha",
        "lambda_per_1000_contracts",
        "lambda_se_hac",
        "lambda_se_classical",
        "lambda_t_hac",
        "lambda_p_value_hac",
        "lambda_ci95_low_hac",
        "lambda_ci95_high_hac",
        "r_squared",
        "residual_std",
        "degrees_freedom",
        "hac_lags",
    ]
    for column in numeric_outputs:
        if column not in estimates:
            estimates[column] = np.nan
    return estimates.sort_values(
        [
            "bucket_seconds",
            "price_transform",
            "include_intercept",
            "game_id",
            "platform",
        ]
    )


def exact_sign_test_two_sided(differences: pd.Series) -> float:
    nonzero = differences.dropna()
    nonzero = nonzero.loc[~np.isclose(nonzero, 0.0)]
    n = len(nonzero)
    if n == 0:
        return 1.0
    positives = int(nonzero.gt(0).sum())
    tail = sum(
        math.comb(n, index) for index in range(0, min(positives, n - positives) + 1)
    ) / (2**n)
    return min(1.0, 2.0 * tail)


def paired_t_statistics(differences: pd.Series) -> dict[str, float]:
    values = differences.dropna().to_numpy(float)
    n = len(values)
    if n <= 1:
        return {
            "difference_se": np.nan,
            "difference_t_stat": np.nan,
            "paired_t_p_value": np.nan,
            "difference_ci95_low": np.nan,
            "difference_ci95_high": np.nan,
        }
    mean = float(np.mean(values))
    standard_error = float(np.std(values, ddof=1) / math.sqrt(n))
    degrees_freedom = n - 1
    t_stat = mean / standard_error if standard_error > 0 else np.nan
    critical = student_t_critical_975(degrees_freedom)
    return {
        "difference_se": standard_error,
        "difference_t_stat": t_stat,
        "paired_t_p_value": student_t_two_sided_p(
            t_stat,
            degrees_freedom,
        ),
        "difference_ci95_low": mean - critical * standard_error,
        "difference_ci95_high": mean + critical * standard_error,
    }


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    output = pd.Series(np.nan, index=p_values.index, dtype=float)
    valid = p_values.dropna().sort_values()
    m = len(valid)
    if m == 0:
        return output
    adjusted = valid.to_numpy(float) * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    output.loc[valid.index] = np.minimum(adjusted, 1.0)
    return output


def summarize_platforms(estimates: pd.DataFrame) -> pd.DataFrame:
    valid = estimates.loc[estimates["status"].eq("ok")].copy()
    group_columns = [
        "bucket_seconds",
        "price_transform",
        "include_intercept",
        "specification",
        "is_primary_specification",
        "platform",
    ]
    summary = valid.groupby(group_columns, dropna=False).agg(
        n_games=("game_id", "nunique"),
        lambda_mean=("lambda_per_1000_contracts", "mean"),
        lambda_median=("lambda_per_1000_contracts", "median"),
        lambda_std=("lambda_per_1000_contracts", "std"),
        lambda_q25=(
            "lambda_per_1000_contracts",
            lambda values: values.quantile(0.25),
        ),
        lambda_q75=(
            "lambda_per_1000_contracts",
            lambda values: values.quantile(0.75),
        ),
        share_positive_lambda=(
            "lambda_per_1000_contracts",
            lambda values: values.gt(0).mean(),
        ),
        median_r_squared=("r_squared", "median"),
        median_observations=("n_observations", "median"),
    ).reset_index()
    return summary.sort_values(group_columns)


def compare_platforms(estimates: pd.DataFrame) -> pd.DataFrame:
    valid = estimates.loc[estimates["status"].eq("ok")].copy()
    specification_columns = [
        "bucket_seconds",
        "price_transform",
        "include_intercept",
        "specification",
        "is_primary_specification",
    ]
    records: list[dict[str, object]] = []
    for specification, group in valid.groupby(
        specification_columns,
        dropna=False,
        sort=True,
    ):
        pivot = group.pivot(
            index="game_id",
            columns="platform",
            values="lambda_per_1000_contracts",
        ).dropna(subset=["kalshi", "polymarket"])
        difference = pivot["kalshi"] - pivot["polymarket"]
        t_stats = paired_t_statistics(difference)
        common = dict(zip(specification_columns, specification))
        records.append(
            {
                **common,
                "n_paired_games": len(pivot),
                "kalshi_mean_lambda": pivot["kalshi"].mean(),
                "kalshi_median_lambda": pivot["kalshi"].median(),
                "polymarket_mean_lambda": pivot["polymarket"].mean(),
                "polymarket_median_lambda": pivot["polymarket"].median(),
                "mean_kalshi_minus_polymarket_lambda": difference.mean(),
                "median_kalshi_minus_polymarket_lambda": difference.median(),
                "share_games_kalshi_lambda_greater": difference.gt(0).mean(),
                "paired_sign_test_p_value": exact_sign_test_two_sided(
                    difference
                ),
                "kalshi_positive_lambda_games": int(
                    pivot["kalshi"].gt(0).sum()
                ),
                "polymarket_positive_lambda_games": int(
                    pivot["polymarket"].gt(0).sum()
                ),
                **t_stats,
            }
        )
    comparison = pd.DataFrame(records)
    comparison["paired_t_p_value_fdr_bh"] = benjamini_hochberg(
        comparison["paired_t_p_value"]
    )
    comparison["lower_lambda_platform_by_mean"] = np.where(
        comparison["mean_kalshi_minus_polymarket_lambda"].lt(0),
        "kalshi",
        np.where(
            comparison["mean_kalshi_minus_polymarket_lambda"].gt(0),
            "polymarket",
            "equal",
        ),
    )
    return comparison.sort_values(specification_columns)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate Kyle lambda separately for each selected game outcome "
            "and platform, then compare matched game-level lambdas. The "
            "primary specification uses 60-second probability changes with "
            "no intercept. Robustness variants add an intercept, log-odds "
            "changes, and 300-second buckets."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=SCRIPT_PROJECT_ROOT,
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=MIN_OBSERVATIONS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if args.min_observations < 3:
        raise ValueError("Minimum observations must be at least three")
    results_dir = root / "data/results"
    panel_path = results_dir / "regression_input.csv"
    panel_diagnostics_path = (
        results_dir / "regression_panel_diagnostics.csv"
    )
    estimates_output = results_dir / "kyle_lambda_estimates.csv"
    summary_output = results_dir / "kyle_lambda_platform_summary.csv"
    comparison_output = (
        results_dir / "kyle_lambda_paired_comparison.csv"
    )

    require_file(panel_path)
    require_file(panel_diagnostics_path)
    panel = pd.read_csv(
        panel_path,
        dtype={
            "game_id": "string",
            "selected_team": "string",
            "platform": "string",
            "instrument_id": "string",
        },
    )
    diagnostics = pd.read_csv(
        panel_diagnostics_path,
        dtype={
            "game_id": "string",
            "selected_team": "string",
            "platform": "string",
            "instrument_id": "string",
        },
    )
    required_panel = {
        "bucket_seconds",
        "game_id",
        "selected_team",
        "platform",
        "instrument_id",
        "time_bucket_index",
        "delta_mid_price",
        "delta_log_odds",
        "signed_volume_thousands",
    }
    missing = required_panel - set(panel.columns)
    if missing:
        raise ValueError(
            f"Regression panel is missing columns: {sorted(missing)}"
        )
    if panel.duplicated(
        [
            "bucket_seconds",
            "game_id",
            "platform",
            "instrument_id",
            "time_bucket_index",
        ]
    ).any():
        raise ValueError("Regression panel contains duplicate observations")

    estimates = estimate_lambdas(
        panel,
        diagnostics,
        args.min_observations,
    )
    platform_summary = summarize_platforms(estimates)
    comparison = compare_platforms(estimates)

    primary = comparison.loc[
        comparison["is_primary_specification"]
    ]
    if len(primary) != 1:
        raise ValueError(
            "Expected exactly one primary paired-comparison result"
        )
    estimates.to_csv(estimates_output, index=False)
    platform_summary.to_csv(summary_output, index=False)
    comparison.to_csv(comparison_output, index=False)

    primary_row = primary.iloc[0]
    print(f"Game-platform regression specifications: {len(estimates):,}")
    print(
        "Successful estimates by specification: "
        f"{estimates.loc[estimates.status.eq('ok')].groupby(['bucket_seconds', 'price_transform', 'specification']).size().to_dict()}"
    )
    print(
        "Primary paired games: "
        f"{int(primary_row['n_paired_games']):,}"
    )
    print(
        "Primary mean lambda difference (Kalshi - Polymarket): "
        f"{primary_row['mean_kalshi_minus_polymarket_lambda']:.8g}"
    )
    print(
        "Primary paired t-test p-value: "
        f"{primary_row['paired_t_p_value']:.6g}"
    )
    print(f"Saved: {estimates_output}")
    print(f"Saved: {summary_output}")
    print(f"Saved: {comparison_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
