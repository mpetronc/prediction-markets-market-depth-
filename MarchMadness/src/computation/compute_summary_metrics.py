from pathlib import Path
import json
import logging
import sys
import time

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

KALSHI_BBO_PATH = (
    ROOT / "data/processed/kalshi/filtered_bbo_trades/bbo_filtered.csv"
)
POLYMARKET_BBO_PATH = (
    ROOT / "data/processed/polymarket/filtered_bbo_trades/bbo_filtered.csv"
)

KALSHI_TRADES_PATH = (
    ROOT / "data/processed/kalshi/filtered_bbo_trades/trades_filtered.csv"
)
POLYMARKET_TRADES_PATH = (
    ROOT / "data/processed/polymarket/filtered_bbo_trades/trades_filtered.csv"
)

RESULTS_DIR = ROOT / "data/results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = RESULTS_DIR / "compute_summary_metrics.log"

SNAPSHOT_OUTPUT_CSV = RESULTS_DIR / "bbo_sampled_price_impact_metrics.csv"
SNAPSHOT_OUTPUT_PARQUET = RESULTS_DIR / "bbo_sampled_price_impact_metrics.parquet"

BY_GAME_OUTPUT = RESULTS_DIR / "summary_metrics_by_game_platform.csv"
BY_INSTRUMENT_OUTPUT = RESULTS_DIR / "summary_metrics_by_instrument_platform.csv"


N_PRICE_IMPACT_SNAPSHOTS_PER_INSTRUMENT = 1000
PRICE_IMPACT_SIZES = (100, 500, 1000)
DEPTH_PROGRESS_EVERY = 50_000


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("summary_metrics")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


LOGGER = setup_logger()


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")


def to_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def safe_std(series: pd.Series) -> float:
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) <= 1:
        return np.nan
    return float(series.std())


def fill_rate(series: pd.Series) -> float:
    if len(series) == 0:
        return np.nan
    return float(series.notna().mean())


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

    counts = {}
    for unit, mask in unit_masks.items():
        counts[unit] = int(mask.sum())
        if mask.any():
            parsed.loc[mask] = pd.to_datetime(
                numeric.loc[mask],
                unit=unit,
                utc=True,
                errors="coerce",
            )

    text_mask = numeric.isna() & raw.notna() & raw.ne("")
    counts["text"] = int(text_mask.sum())
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

    LOGGER.info(
        "Parsed mixed timestamp column with counts=%s. Parsed range: %s -> %s",
        counts,
        parsed.min(),
        parsed.max(),
    )

    return parsed


def normalize_prices_by_platform(
    df: pd.DataFrame,
    price_cols: list[str],
) -> pd.DataFrame:

    df = df.copy()

    for platform in df["platform"].dropna().unique():
        mask = df["platform"].eq(platform)

        for col in price_cols:
            if col not in df.columns:
                continue

            s = pd.to_numeric(df.loc[mask, col], errors="coerce")

            if s.dropna().empty:
                continue

            median = s.dropna().median()

            if median > 1:
                df.loc[mask, col] = s / 100.0
                LOGGER.info(
                    "Normalized %s.%s from cents to decimals. Median before: %.6f",
                    platform,
                    col,
                    median,
                )
            else:
                df.loc[mask, col] = s
                LOGGER.info(
                    "Kept %s.%s as decimals. Median: %.6f",
                    platform,
                    col,
                    median,
                )

    return df


def cast_id_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()

    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype("string")

    return df


def load_bbo() -> pd.DataFrame:
    require_file(KALSHI_BBO_PATH)
    require_file(POLYMARKET_BBO_PATH)

    LOGGER.info("Reading Kalshi BBO: %s", KALSHI_BBO_PATH)
    kalshi = pd.read_csv(KALSHI_BBO_PATH)

    LOGGER.info("Reading Polymarket BBO: %s", POLYMARKET_BBO_PATH)
    polymarket = pd.read_csv(POLYMARKET_BBO_PATH)

    bbo = pd.concat([kalshi, polymarket], ignore_index=True)

    required_cols = [
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "timestamp",
        "best_bid",
        "best_ask",
        "mid_price",
        "bid_depth",
        "ask_depth",
        "raw_bids",
        "raw_asks",
    ]

    missing = [c for c in required_cols if c not in bbo.columns]
    if missing:
        raise ValueError(f"BBO files missing columns: {missing}")

    bbo = cast_id_columns(
        bbo,
        ["game_id", "platform", "team", "instrument_id"],
    )

    bbo["timestamp"] = parse_timestamp_column(bbo["timestamp"])

    bbo = to_numeric(
        bbo,
        [
            "best_bid",
            "best_ask",
            "mid_price",
            "bid_depth",
            "ask_depth",
        ],
    )

    bbo = normalize_prices_by_platform(
        bbo,
        ["best_bid", "best_ask", "mid_price"],
    )


    before = len(bbo)

    bbo = bbo.dropna(subset=["timestamp", "best_bid", "best_ask"])
    bbo = bbo[(bbo["best_bid"] > 0) & (bbo["best_ask"] > 0)].copy()


    bbo["mid_price"] = (bbo["best_bid"] + bbo["best_ask"]) / 2.0
    bbo["spread"] = bbo["best_ask"] - bbo["best_bid"]


    bbo = bbo[
        (bbo["best_bid"] >= 0)
        & (bbo["best_bid"] <= 1)
        & (bbo["best_ask"] >= 0)
        & (bbo["best_ask"] <= 1)
        & (bbo["spread"] >= 0)
        & (bbo["spread"] <= 1)
    ].copy()

    after = len(bbo)

    LOGGER.info(
        "Cleaned BBO rows from %s to %s. Removed %s rows.",
        f"{before:,}",
        f"{after:,}",
        f"{before - after:,}",
    )


    bbo = add_order_book_depth_metrics(bbo)

    return bbo


def load_trades() -> pd.DataFrame:
    require_file(KALSHI_TRADES_PATH)
    require_file(POLYMARKET_TRADES_PATH)

    LOGGER.info("Reading Kalshi trades: %s", KALSHI_TRADES_PATH)
    kalshi = pd.read_csv(KALSHI_TRADES_PATH)

    LOGGER.info("Reading Polymarket trades: %s", POLYMARKET_TRADES_PATH)
    polymarket = pd.read_csv(POLYMARKET_TRADES_PATH)

    trades = pd.concat([kalshi, polymarket], ignore_index=True)

    required_cols = [
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "trade_id",
        "timestamp",
        "price",
        "size",
        "signed_size",
        "notional",
        "side",
    ]

    missing = [c for c in required_cols if c not in trades.columns]
    if missing:
        raise ValueError(f"Trade files missing columns: {missing}")

    trades = cast_id_columns(
        trades,
        ["game_id", "platform", "team", "instrument_id", "trade_id"],
    )

    trades["timestamp"] = parse_timestamp_column(trades["timestamp"])

    trades = to_numeric(
        trades,
        [
            "price",
            "yes_price",
            "no_price",
            "size",
            "signed_size",
            "notional",
            "fee_usd",
        ],
    )


    trades = normalize_prices_by_platform(trades, ["price", "yes_price", "no_price"])

    kalshi_mask = trades["platform"].eq("kalshi")
    missing_yes_price = kalshi_mask & trades["yes_price"].isna()
    recoverable_yes_price = missing_yes_price & trades["no_price"].notna()
    trades.loc[recoverable_yes_price, "yes_price"] = (
        1.0 - trades.loc[recoverable_yes_price, "no_price"]
    )
    unresolved_yes_price = kalshi_mask & trades["yes_price"].isna()
    if unresolved_yes_price.any():
        raise ValueError(
            "Kalshi trades contain rows without a recoverable YES price: "
            f"{int(unresolved_yes_price.sum()):,}"
        )

    # Every instrument represents the selected team's YES outcome. A Kalshi
    # NO-side taker is negative order flow in that same outcome, but its
    # probability must still be expressed as the YES price.
    trades.loc[kalshi_mask, "price"] = trades.loc[
        kalshi_mask, "yes_price"
    ]

    before = len(trades)

    trades = trades.dropna(subset=["timestamp", "price", "size"])
    trades = trades[
        (trades["price"] >= 0)
        & (trades["price"] <= 1)
        & (trades["size"] > 0)
    ].copy()

    after = len(trades)

    LOGGER.info(
        "Cleaned trade rows from %s to %s. Removed %s rows.",
        f"{before:,}",
        f"{after:,}",
        f"{before - after:,}",
    )


    missing_notional = trades["notional"].isna()

    if missing_notional.any():
        trades.loc[missing_notional, "notional"] = (
            trades.loc[missing_notional, "price"]
            * trades.loc[missing_notional, "size"]
        )

    kalshi_mask = trades["platform"].eq("kalshi")
    trades.loc[kalshi_mask, "notional"] = (
        trades.loc[kalshi_mask, "price"]
        * trades.loc[kalshi_mask, "size"]
    )

    polymarket_mask = trades["platform"].eq("polymarket")
    polymarket_side = (
        trades.loc[polymarket_mask, "side"]
        .astype("string")
        .str.strip()
        .str.upper()
    )
    unexpected_polymarket_side = ~polymarket_side.isin(["BUY", "SELL"])
    if unexpected_polymarket_side.any():
        unexpected = sorted(
            polymarket_side.loc[unexpected_polymarket_side]
            .dropna()
            .unique()
        )
        raise ValueError(
            "Polymarket trades contain unexpected maker-side values: "
            f"{unexpected}"
        )
    # Predexon labels Polymarket trades with the maker's side. Signed order
    # flow must use the aggressive/taker direction: maker BUY is taker sell,
    # and maker SELL is taker buy.
    maker_buy_index = polymarket_side.index[polymarket_side.eq("BUY")]
    maker_sell_index = polymarket_side.index[polymarket_side.eq("SELL")]
    trades.loc[maker_buy_index, "signed_size"] = -trades.loc[
        maker_buy_index,
        "size",
    ]
    trades.loc[maker_sell_index, "signed_size"] = trades.loc[
        maker_sell_index,
        "size",
    ]
    trades["signed_notional"] = np.sign(trades["signed_size"]) * trades["notional"]

    LOGGER.info(
        "Oriented %s Kalshi trade rows to YES-outcome prices; NO-side share %.2f%%.",
        f"{int(kalshi_mask.sum()):,}",
        100.0
        * float(
            trades.loc[kalshi_mask, "side"]
            .astype("string")
            .str.lower()
            .eq("no")
            .mean()
        ),
    )

    return trades


def sample_bbo_snapshots_per_instrument(
    bbo: pd.DataFrame,
    n_snapshots: int = 10,
) -> pd.DataFrame:

    group_cols = ["game_id", "platform", "team", "instrument_id"]

    before = len(bbo)

    bbo_sorted = bbo.sort_values(group_cols + ["timestamp"]).copy()

    sampled_parts = []

    for _, group in bbo_sorted.groupby(group_cols, dropna=False, sort=False):
        if len(group) <= n_snapshots:
            sampled_parts.append(group)
        else:
            positions = np.linspace(
                0,
                len(group) - 1,
                n_snapshots,
                dtype=int,
            )
            sampled_parts.append(group.iloc[positions])

    sampled = pd.concat(sampled_parts, ignore_index=True)

    after = len(sampled)

    LOGGER.info(
        "Sampled BBO from %s rows to %s rows using %s snapshots per instrument.",
        f"{before:,}",
        f"{after:,}",
        n_snapshots,
    )

    LOGGER.info(
        "Sampled BBO games: %s | platforms: %s | instruments: %s",
        f"{sampled['game_id'].nunique():,}",
        sorted(sampled["platform"].dropna().unique()),
        f"{sampled['instrument_id'].nunique():,}",
    )

    return sampled


def parse_book(raw_book) -> list:
    if pd.isna(raw_book):
        return []

    if isinstance(raw_book, list):
        return raw_book

    try:
        parsed = json.loads(raw_book)
        if isinstance(parsed, list):
            return parsed
        return []
    except Exception:
        return []


def normalize_book_levels(raw_book, platform: str) -> list[tuple[float, float]]:

    levels = parse_book(raw_book)
    out = []

    if platform not in {"kalshi", "polymarket"}:
        raise ValueError(f"Unknown platform for order-book normalization: {platform}")

    for level in levels:
        try:
            if isinstance(level, dict):
                price = float(level["price"])
                size = float(level["size"])
            elif isinstance(level, (list, tuple)) and len(level) >= 2:
                price = float(level[0])
                size = float(level[1])
            else:
                continue

            # Predexon stores Kalshi raw book levels in integer cents, including
            # the ambiguous value 1 for one cent. Polymarket levels are already
            # decimal probabilities, where 1 correctly means one dollar.
            if platform == "kalshi":
                price = price / 100.0

            if 0 <= price <= 1 and size > 0:
                out.append((price, size))
        except Exception:
            continue

    return out


def summarize_book_side(
    levels: list[tuple[float, float]],
    best_price: float,
) -> tuple[float, float, float, float]:
    if not levels:
        return np.nan, np.nan, np.nan, np.nan

    full_depth = float(sum(size for _, size in levels))
    full_notional = float(sum(price * size for price, size in levels))
    best_depth = float(
        sum(
            size
            for price, size in levels
            if np.isclose(price, best_price, rtol=1e-9, atol=1e-12)
        )
    )
    if best_depth <= 0:
        best_depth = np.nan
        best_notional = np.nan
    else:
        best_notional = float(best_price * best_depth)

    return best_depth, full_depth, best_notional, full_notional


def add_order_book_depth_metrics(
    bbo: pd.DataFrame,
    progress_every: int = DEPTH_PROGRESS_EVERY,
) -> pd.DataFrame:
    bbo = bbo.reset_index(drop=True).copy()
    n = len(bbo)
    arrays = {
        "best_bid_depth": np.full(n, np.nan),
        "best_ask_depth": np.full(n, np.nan),
        "full_bid_depth": np.full(n, np.nan),
        "full_ask_depth": np.full(n, np.nan),
        "best_bid_depth_notional": np.full(n, np.nan),
        "best_ask_depth_notional": np.full(n, np.nan),
        "full_bid_depth_notional": np.full(n, np.nan),
        "full_ask_depth_notional": np.full(n, np.nan),
    }
    stored_depth_mismatches = 0
    unmatched_best_levels = 0
    start = time.time()

    selected_cols = [
        "platform",
        "raw_bids",
        "raw_asks",
        "best_bid",
        "best_ask",
        "bid_depth",
        "ask_depth",
    ]
    for i, row in enumerate(
        bbo[selected_cols].itertuples(index=False),
        start=0,
    ):
        bids = normalize_book_levels(row.raw_bids, row.platform)
        asks = normalize_book_levels(row.raw_asks, row.platform)
        (
            best_bid_depth,
            full_bid_depth,
            best_bid_notional,
            full_bid_notional,
        ) = summarize_book_side(bids, row.best_bid)
        (
            best_ask_depth,
            full_ask_depth,
            best_ask_notional,
            full_ask_notional,
        ) = summarize_book_side(asks, row.best_ask)

        arrays["best_bid_depth"][i] = best_bid_depth
        arrays["best_ask_depth"][i] = best_ask_depth
        arrays["full_bid_depth"][i] = full_bid_depth
        arrays["full_ask_depth"][i] = full_ask_depth
        arrays["best_bid_depth_notional"][i] = best_bid_notional
        arrays["best_ask_depth_notional"][i] = best_ask_notional
        arrays["full_bid_depth_notional"][i] = full_bid_notional
        arrays["full_ask_depth_notional"][i] = full_ask_notional

        if pd.isna(best_bid_depth) or pd.isna(best_ask_depth):
            unmatched_best_levels += 1

        for stored, reconstructed in [
            (row.bid_depth, full_bid_depth),
            (row.ask_depth, full_ask_depth),
        ]:
            if pd.notna(stored) and pd.notna(reconstructed):
                tolerance = max(1e-6, 1e-8 * abs(reconstructed))
                if abs(float(stored) - reconstructed) > tolerance:
                    stored_depth_mismatches += 1

        completed = i + 1
        if completed % progress_every == 0 or completed == n:
            elapsed = time.time() - start
            LOGGER.info(
                "Depth reconstruction progress: %s/%s rows | %.2f%% | %.0f rows/sec",
                f"{completed:,}",
                f"{n:,}",
                100.0 * completed / n if n else 100.0,
                completed / elapsed if elapsed > 0 else np.nan,
            )

    for column, values in arrays.items():
        bbo[column] = values

    if unmatched_best_levels:
        raise ValueError(
            "Could not match the stored best quote to a raw-book level for "
            f"{unmatched_best_levels:,} BBO rows"
        )
    if stored_depth_mismatches:
        LOGGER.warning(
            "Stored full-depth values differed from raw-book sums in %s side-rows; "
            "the reconstructed raw-book values are used.",
            f"{stored_depth_mismatches:,}",
        )

    # Preserve the established generic column names as explicit full-book
    # aliases so downstream code remains compatible.
    bbo["bid_depth"] = bbo["full_bid_depth"]
    bbo["ask_depth"] = bbo["full_ask_depth"]
    bbo["bid_depth_notional"] = bbo["full_bid_depth_notional"]
    bbo["ask_depth_notional"] = bbo["full_ask_depth_notional"]

    return bbo


def avg_execution_price(
    levels: list[tuple[float, float]],
    q: float,
    side: str,
) -> float:

    if not levels:
        return np.nan

    if side == "buy":
        levels = sorted(levels, key=lambda x: x[0])
    elif side == "sell":
        levels = sorted(levels, key=lambda x: x[0], reverse=True)
    else:
        raise ValueError(f"Unknown side: {side}")

    remaining = q
    total_value = 0.0
    filled = 0.0

    for price, size in levels:
        take = min(size, remaining)
        total_value += take * price
        filled += take
        remaining -= take

        if remaining <= 0:
            break

    if filled < q:
        return np.nan

    return total_value / q


def compute_row_price_impacts(
    raw_bids,
    raw_asks,
    best_bid: float,
    best_ask: float,
    platform: str,
    sizes: tuple[int, ...],
) -> dict:

    bids = normalize_book_levels(raw_bids, platform=platform)
    asks = normalize_book_levels(raw_asks, platform=platform)

    result = {}

    for q in sizes:
        buy_avg = avg_execution_price(asks, q, side="buy")
        sell_avg = avg_execution_price(bids, q, side="sell")

        result[f"buy_avg_price_{q}"] = buy_avg
        result[f"sell_avg_price_{q}"] = sell_avg

        if pd.notna(buy_avg):
            buy_impact = buy_avg - best_ask
        else:
            buy_impact = np.nan

        if pd.notna(sell_avg):
            sell_impact = best_bid - sell_avg
        else:
            sell_impact = np.nan


        eps = 1e-9

        if pd.notna(buy_impact) and -eps <= buy_impact < 0:
            buy_impact = 0.0

        if pd.notna(sell_impact) and -eps <= sell_impact < 0:
            sell_impact = 0.0


        if pd.notna(buy_impact) and buy_impact < 0:
            buy_impact = np.nan

        if pd.notna(sell_impact) and sell_impact < 0:
            sell_impact = np.nan

        result[f"buy_impact_{q}"] = buy_impact
        result[f"sell_impact_{q}"] = sell_impact

    return result


def add_price_impact_metrics(
    bbo_sample: pd.DataFrame,
    sizes: tuple[int, ...] = PRICE_IMPACT_SIZES,
    progress_every: int = 50_000,
) -> pd.DataFrame:

    bbo_sample = bbo_sample.copy()
    n = len(bbo_sample)

    LOGGER.info(
        "Computing sampled price-impact metrics for %s rows and sizes=%s",
        f"{n:,}",
        sizes,
    )

    start = time.time()
    impact_rows = []

    selected_cols = [
        "platform",
        "raw_bids",
        "raw_asks",
        "best_bid",
        "best_ask",
    ]

    for i, row in enumerate(bbo_sample[selected_cols].itertuples(index=False), start=1):
        impact_rows.append(
            compute_row_price_impacts(
                raw_bids=row.raw_bids,
                raw_asks=row.raw_asks,
                best_bid=row.best_bid,
                best_ask=row.best_ask,
                platform=row.platform,
                sizes=sizes,
            )
        )

        if i % progress_every == 0 or i == n:
            elapsed = time.time() - start
            rows_per_sec = i / elapsed if elapsed > 0 else np.nan
            remaining = n - i
            eta_seconds = remaining / rows_per_sec if rows_per_sec and rows_per_sec > 0 else np.nan

            LOGGER.info(
                "Price impact progress: %s/%s rows | %.2f%% | %.0f rows/sec | ETA %.1f min",
                f"{i:,}",
                f"{n:,}",
                (i / n) * 100 if n else 100,
                rows_per_sec,
                eta_seconds / 60 if pd.notna(eta_seconds) else np.nan,
            )

    impact_df = pd.DataFrame(impact_rows)

    bbo_sample = pd.concat(
        [
            bbo_sample.reset_index(drop=True),
            impact_df.reset_index(drop=True),
        ],
        axis=1,
    )

    LOGGER.info(
        "Finished sampled price-impact computation in %.2f minutes",
        (time.time() - start) / 60,
    )

    return bbo_sample


def build_price_impact_snapshot_metrics(bbo_sample: pd.DataFrame) -> pd.DataFrame:
    snapshot = add_price_impact_metrics(
        bbo_sample,
        sizes=PRICE_IMPACT_SIZES,
    )

    keep_cols = [
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "timestamp",

        "best_bid",
        "best_ask",
        "mid_price",
        "spread",
        "bid_depth",
        "ask_depth",
        "bid_depth_notional",
        "ask_depth_notional",
        "best_bid_depth",
        "best_ask_depth",
        "full_bid_depth",
        "full_ask_depth",
        "best_bid_depth_notional",
        "best_ask_depth_notional",
        "full_bid_depth_notional",
        "full_ask_depth_notional",

        "raw_bids",
        "raw_asks",

        "buy_avg_price_100",
        "sell_avg_price_100",
        "buy_impact_100",
        "sell_impact_100",

        "buy_avg_price_500",
        "sell_avg_price_500",
        "buy_impact_500",
        "sell_impact_500",

        "buy_avg_price_1000",
        "sell_avg_price_1000",
        "buy_impact_1000",
        "sell_impact_1000",
    ]

    keep_cols = [c for c in keep_cols if c in snapshot.columns]

    snapshot = snapshot[keep_cols].copy()

    return snapshot


def compute_full_bbo_metrics(
    bbo: pd.DataFrame,
    group_cols: list[str],
) -> pd.DataFrame:

    grouped = bbo.groupby(group_cols, dropna=False)

    metrics = grouped.agg(
        bbo_observations=("timestamp", "count"),
        num_markets=("instrument_id", "nunique"),

        first_bbo_timestamp=("timestamp", "min"),
        last_bbo_timestamp=("timestamp", "max"),

        avg_best_bid=("best_bid", "mean"),
        avg_best_ask=("best_ask", "mean"),

        avg_mid_price=("mid_price", "mean"),
        median_mid_price=("mid_price", "median"),
        mid_price_volatility=("mid_price", safe_std),

        avg_spread=("spread", "mean"),
        median_spread=("spread", "median"),

        avg_bid_depth=("bid_depth", "mean"),
        median_bid_depth=("bid_depth", "median"),

        avg_ask_depth=("ask_depth", "mean"),
        median_ask_depth=("ask_depth", "median"),

        avg_bid_depth_notional=("bid_depth_notional", "mean"),
        median_bid_depth_notional=("bid_depth_notional", "median"),

        avg_ask_depth_notional=("ask_depth_notional", "mean"),
        median_ask_depth_notional=("ask_depth_notional", "median"),

        avg_best_bid_depth=("best_bid_depth", "mean"),
        median_best_bid_depth=("best_bid_depth", "median"),

        avg_best_ask_depth=("best_ask_depth", "mean"),
        median_best_ask_depth=("best_ask_depth", "median"),

        avg_full_bid_depth=("full_bid_depth", "mean"),
        median_full_bid_depth=("full_bid_depth", "median"),

        avg_full_ask_depth=("full_ask_depth", "mean"),
        median_full_ask_depth=("full_ask_depth", "median"),

        avg_best_bid_depth_notional=("best_bid_depth_notional", "mean"),
        median_best_bid_depth_notional=(
            "best_bid_depth_notional",
            "median",
        ),

        avg_best_ask_depth_notional=("best_ask_depth_notional", "mean"),
        median_best_ask_depth_notional=(
            "best_ask_depth_notional",
            "median",
        ),

        avg_full_bid_depth_notional=("full_bid_depth_notional", "mean"),
        median_full_bid_depth_notional=(
            "full_bid_depth_notional",
            "median",
        ),

        avg_full_ask_depth_notional=("full_ask_depth_notional", "mean"),
        median_full_ask_depth_notional=(
            "full_ask_depth_notional",
            "median",
        ),
    ).reset_index()

    return metrics


def compute_price_impact_metrics(
    snapshot: pd.DataFrame,
    group_cols: list[str],
) -> pd.DataFrame:

    grouped = snapshot.groupby(group_cols, dropna=False)

    metrics = grouped.agg(
        impact_sample_observations=("timestamp", "count"),

        avg_buy_impact_100=("buy_impact_100", "mean"),
        median_buy_impact_100=("buy_impact_100", "median"),
        fill_rate_buy_100=("buy_avg_price_100", fill_rate),

        avg_sell_impact_100=("sell_impact_100", "mean"),
        median_sell_impact_100=("sell_impact_100", "median"),
        fill_rate_sell_100=("sell_avg_price_100", fill_rate),

        avg_buy_impact_500=("buy_impact_500", "mean"),
        median_buy_impact_500=("buy_impact_500", "median"),
        fill_rate_buy_500=("buy_avg_price_500", fill_rate),

        avg_sell_impact_500=("sell_impact_500", "mean"),
        median_sell_impact_500=("sell_impact_500", "median"),
        fill_rate_sell_500=("sell_avg_price_500", fill_rate),

        avg_buy_impact_1000=("buy_impact_1000", "mean"),
        median_buy_impact_1000=("buy_impact_1000", "median"),
        fill_rate_buy_1000=("buy_avg_price_1000", fill_rate),

        avg_sell_impact_1000=("sell_impact_1000", "mean"),
        median_sell_impact_1000=("sell_impact_1000", "median"),
        fill_rate_sell_1000=("sell_avg_price_1000", fill_rate),
    ).reset_index()

    return metrics


def compute_trade_metrics(
    trades: pd.DataFrame,
    group_cols: list[str],
) -> pd.DataFrame:
    grouped = trades.groupby(group_cols, dropna=False)

    metrics = grouped.agg(
        num_trades=("trade_id", "count"),

        first_trade_timestamp=("timestamp", "min"),
        last_trade_timestamp=("timestamp", "max"),

        total_volume=("size", "sum"),
        avg_trade_size=("size", "mean"),
        median_trade_size=("size", "median"),

        total_signed_volume=("signed_size", "sum"),
        total_abs_signed_volume=("signed_size", lambda x: x.abs().sum()),

        total_notional=("notional", "sum"),
        total_signed_notional=("signed_notional", "sum"),

        avg_trade_price=("price", "mean"),
        median_trade_price=("price", "median"),
        trade_price_volatility=("price", safe_std),
    ).reset_index()

    return metrics


def compute_summary(
    full_bbo: pd.DataFrame,
    impact_snapshot: pd.DataFrame,
    trades: pd.DataFrame,
    group_cols: list[str],
) -> pd.DataFrame:
    full_bbo_metrics = compute_full_bbo_metrics(full_bbo, group_cols)
    impact_metrics = compute_price_impact_metrics(impact_snapshot, group_cols)
    trade_metrics = compute_trade_metrics(trades, group_cols)

    summary = pd.merge(
        full_bbo_metrics,
        impact_metrics,
        on=group_cols,
        how="outer",
    )

    summary = pd.merge(
        summary,
        trade_metrics,
        on=group_cols,
        how="outer",
    )

    summary = summary.sort_values(group_cols).reset_index(drop=True)

    return summary


def save_outputs(
    impact_snapshot: pd.DataFrame,
    by_instrument: pd.DataFrame,
    by_game: pd.DataFrame,
) -> None:
    LOGGER.info("Saving sampled price-impact snapshot CSV: %s", SNAPSHOT_OUTPUT_CSV)
    impact_snapshot.to_csv(SNAPSHOT_OUTPUT_CSV, index=False)

    LOGGER.info("Saving instrument summary: %s", BY_INSTRUMENT_OUTPUT)
    by_instrument.to_csv(BY_INSTRUMENT_OUTPUT, index=False)

    LOGGER.info("Saving game/platform summary: %s", BY_GAME_OUTPUT)
    by_game.to_csv(BY_GAME_OUTPUT, index=False)

    parquet_temporary = SNAPSHOT_OUTPUT_PARQUET.with_suffix(".parquet.tmp")
    LOGGER.info(
        "Saving sampled price-impact snapshot parquet: %s",
        SNAPSHOT_OUTPUT_PARQUET,
    )
    try:
        impact_snapshot.to_parquet(parquet_temporary, index=False)
        parquet_temporary.replace(SNAPSHOT_OUTPUT_PARQUET)
    except Exception as exc:
        parquet_temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "CSV summaries were saved, but the Parquet snapshot could not be "
            "refreshed. Install pyarrow or fastparquet before using the "
            "existing Parquet path."
        ) from exc


def main() -> None:
    total_start = time.time()

    LOGGER.info("ROOT: %s", ROOT)
    LOGGER.info("Log file: %s", LOG_PATH)

    LOGGER.info("Loading BBO data...")
    bbo = load_bbo()

    LOGGER.info("BBO rows after cleaning: %s", f"{len(bbo):,}")
    LOGGER.info("BBO games: %s", f"{bbo['game_id'].nunique():,}")
    LOGGER.info("BBO platforms: %s", sorted(bbo["platform"].dropna().unique()))
    LOGGER.info("BBO timestamp range: %s -> %s", bbo["timestamp"].min(), bbo["timestamp"].max())

    LOGGER.info("Sampling BBO only for expensive price-impact computation...")
    impact_bbo = sample_bbo_snapshots_per_instrument(
        bbo,
        n_snapshots=N_PRICE_IMPACT_SNAPSHOTS_PER_INSTRUMENT,
    )

    LOGGER.info("Building sampled price-impact snapshot metrics...")
    impact_snapshot = build_price_impact_snapshot_metrics(impact_bbo)

    LOGGER.info("Impact snapshot rows: %s", f"{len(impact_snapshot):,}")
    LOGGER.info("Impact snapshot games: %s", f"{impact_snapshot['game_id'].nunique():,}")

    LOGGER.info("Loading trade data...")
    trades = load_trades()

    LOGGER.info("Trade rows after cleaning: %s", f"{len(trades):,}")
    LOGGER.info("Trade games: %s", f"{trades['game_id'].nunique():,}")
    LOGGER.info("Trade platforms: %s", sorted(trades["platform"].dropna().unique()))
    LOGGER.info("Trade timestamp range: %s -> %s", trades["timestamp"].min(), trades["timestamp"].max())

    LOGGER.info("Computing instrument-level summary...")
    instrument_group_cols = ["game_id", "platform", "team", "instrument_id"]
    by_instrument = compute_summary(
        full_bbo=bbo,
        impact_snapshot=impact_snapshot,
        trades=trades,
        group_cols=instrument_group_cols,
    )

    LOGGER.info("Instrument-level rows: %s", f"{len(by_instrument):,}")
    LOGGER.info("Instrument-level games: %s", f"{by_instrument['game_id'].nunique():,}")

    LOGGER.info("Computing per-game/platform summary...")
    game_group_cols = ["game_id", "platform"]
    by_game = compute_summary(
        full_bbo=bbo,
        impact_snapshot=impact_snapshot,
        trades=trades,
        group_cols=game_group_cols,
    )

    LOGGER.info("Per-game/platform rows: %s", f"{len(by_game):,}")
    LOGGER.info("Per-game/platform games: %s", f"{by_game['game_id'].nunique():,}")

    LOGGER.info("Saving outputs...")
    save_outputs(impact_snapshot, by_instrument, by_game)

    LOGGER.info("Saved:")
    LOGGER.info(" - %s", SNAPSHOT_OUTPUT_CSV)
    LOGGER.info(" - %s", SNAPSHOT_OUTPUT_PARQUET)
    LOGGER.info(" - %s", BY_INSTRUMENT_OUTPUT)
    LOGGER.info(" - %s", BY_GAME_OUTPUT)
    LOGGER.info(" - %s", LOG_PATH)

    LOGGER.info("Per-game/platform summary preview:")
    LOGGER.info("\n%s", by_game.head(20).to_string(index=False))

    LOGGER.info(
        "Finished all computations in %.2f minutes",
        (time.time() - total_start) / 60,
    )


if __name__ == "__main__":
    main()
