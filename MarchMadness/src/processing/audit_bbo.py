from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "data/results"
MARKET_MAP_PATH = ROOT / "data/matched_markets/MM_market_map.csv"

BBO_PATHS = {
    "kalshi": ROOT
    / "data/processed/kalshi/filtered_bbo_trades/bbo_filtered.csv",
    "polymarket": ROOT
    / "data/processed/polymarket/filtered_bbo_trades/bbo_filtered.csv",
}

REASON_OUTPUT = RESULTS_DIR / "bbo_audit_reason_counts.csv"
INSTRUMENT_OUTPUT = RESULTS_DIR / "bbo_audit_by_instrument.csv"
SAMPLE_OUTPUT = RESULTS_DIR / "bbo_rejected_samples.csv"

CHUNK_SIZE = 100_000
SAMPLES_PER_REASON = 10


def parse_timestamps(values):
    text_values = values.astype("string").str.strip()
    numeric = pd.to_numeric(text_values, errors="coerce")
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")

    numeric_mask = numeric.notna()
    absolute = numeric.abs()
    units = {
        "s": numeric_mask & absolute.lt(1e11),
        "ms": numeric_mask & absolute.ge(1e11) & absolute.lt(1e14),
        "us": numeric_mask & absolute.ge(1e14) & absolute.lt(1e17),
        "ns": numeric_mask & absolute.ge(1e17),
    }

    for unit, mask in units.items():
        if mask.any():
            result.loc[mask] = pd.to_datetime(
                numeric.loc[mask], unit=unit, utc=True, errors="coerce"
            )

    text_mask = ~numeric_mask & text_values.notna()
    if text_mask.any():
        result.loc[text_mask] = pd.to_datetime(
            text_values.loc[text_mask], utc=True, errors="coerce"
        )

    return result


def normalize_prices(frame, platform):
    for column in ["best_bid", "best_ask"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        values = frame[column].dropna()
        if not values.empty and values.median() > 1:
            frame[column] = frame[column] / 100.0
    return frame


def classify_rows(frame):
    timestamp = frame["parsed_timestamp"]
    bid = frame["best_bid"]
    ask = frame["best_ask"]

    conditions = [
        timestamp.isna(),
        bid.isna(),
        ask.isna(),
        bid.notna() & bid.le(0),
        ask.notna() & ask.le(0),
        bid.notna() & bid.gt(1),
        ask.notna() & ask.gt(1),
        bid.notna() & ask.notna() & bid.gt(ask),
    ]
    labels = [
        "invalid_timestamp",
        "missing_best_bid",
        "missing_best_ask",
        "nonpositive_best_bid",
        "nonpositive_best_ask",
        "best_bid_above_one",
        "best_ask_above_one",
        "crossed_book",
    ]

    frame["rejection_reason"] = np.select(conditions, labels, default="valid")
    frame["spread"] = ask - bid
    return frame


def raw_side_status(value):
    if pd.isna(value):
        return "missing"
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "missing"
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return "unparseable"
    if isinstance(parsed, list) and len(parsed) == 0:
        return "empty"
    if isinstance(parsed, list):
        return "nonempty"
    return "unexpected_type"


def load_expected_instruments():
    if not MARKET_MAP_PATH.exists():
        return pd.DataFrame(
            columns=["platform", "game_id", "team", "instrument_id"]
        )

    market_map = pd.read_csv(MARKET_MAP_PATH, dtype="string")
    rows = []
    definitions = [
        (
            "kalshi",
            "kalshi_ticker_1",
            "kalshi_team_1",
        ),
        (
            "kalshi",
            "kalshi_ticker_2",
            "kalshi_team_2",
        ),
        (
            "polymarket",
            "polymarket_token_id_1",
            "polymarket_team_a",
        ),
        (
            "polymarket",
            "polymarket_token_id_2",
            "polymarket_team_b",
        ),
    ]

    for platform, instrument_column, team_column in definitions:
        if instrument_column not in market_map.columns:
            continue
        part = pd.DataFrame(
            {
                "platform": platform,
                "game_id": market_map["game_id"].astype("string"),
                "team": (
                    market_map[team_column].astype("string")
                    if team_column in market_map.columns
                    else pd.Series(pd.NA, index=market_map.index, dtype="string")
                ),
                "instrument_id": market_map[instrument_column].astype("string"),
            }
        )
        rows.append(part)

    if not rows:
        return pd.DataFrame(
            columns=["platform", "game_id", "team", "instrument_id"]
        )

    expected = pd.concat(rows, ignore_index=True)
    expected["instrument_id"] = expected["instrument_id"].str.strip()
    expected = expected.dropna(subset=["game_id", "instrument_id"])
    return expected.drop_duplicates(
        subset=["platform", "game_id", "instrument_id"]
    )


def audit_platform(platform, path):
    if not path.exists():
        raise FileNotFoundError(f"BBO file not found: {path}")

    available_columns = pd.read_csv(path, nrows=0).columns.tolist()
    required = {
        "game_id",
        "team",
        "instrument_id",
        "timestamp",
        "best_bid",
        "best_ask",
    }
    missing = required - set(available_columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")

    selected = [
        column
        for column in [
            "game_id",
            "platform",
            "team",
            "instrument_id",
            "timestamp",
            "best_bid",
            "best_ask",
            "raw_bids",
            "raw_asks",
        ]
        if column in available_columns
    ]

    all_rows = []
    samples = []
    sample_counts = {}

    for chunk in pd.read_csv(
        path,
        usecols=selected,
        chunksize=CHUNK_SIZE,
        low_memory=False,
        dtype={
            "game_id": "string",
            "platform": "string",
            "team": "string",
            "instrument_id": "string",
        },
    ):
        chunk["platform"] = platform
        chunk["instrument_id"] = chunk["instrument_id"].astype("string").str.strip()
        chunk["parsed_timestamp"] = parse_timestamps(chunk["timestamp"])
        chunk = normalize_prices(chunk, platform)
        chunk = classify_rows(chunk)

        compact = chunk[
            [
                "platform",
                "game_id",
                "team",
                "instrument_id",
                "parsed_timestamp",
                "best_bid",
                "best_ask",
                "spread",
                "rejection_reason",
            ]
        ].copy()
        all_rows.append(compact)

        rejected = chunk[chunk["rejection_reason"] != "valid"]
        for reason, reason_rows in rejected.groupby("rejection_reason"):
            key = (platform, reason)
            remaining = SAMPLES_PER_REASON - sample_counts.get(key, 0)
            if remaining <= 0:
                continue
            sample = reason_rows.head(remaining).copy()
            if "raw_bids" in sample.columns:
                sample["raw_bids_status"] = sample["raw_bids"].map(raw_side_status)
                sample["raw_bids"] = sample["raw_bids"].astype("string").str.slice(0, 2000)
            if "raw_asks" in sample.columns:
                sample["raw_asks_status"] = sample["raw_asks"].map(raw_side_status)
                sample["raw_asks"] = sample["raw_asks"].astype("string").str.slice(0, 2000)
            sample["parsed_timestamp"] = sample["parsed_timestamp"].astype("string")
            samples.append(sample)
            sample_counts[key] = sample_counts.get(key, 0) + len(sample)

    return pd.concat(all_rows, ignore_index=True), samples


def build_reason_counts(rows):
    counts = (
        rows.groupby(["platform", "rejection_reason"], dropna=False)
        .size()
        .rename("rows")
        .reset_index()
    )
    totals = rows.groupby("platform").size().rename("platform_rows")
    counts = counts.join(totals, on="platform")
    counts["percentage_of_platform"] = (
        counts["rows"] / counts["platform_rows"] * 100
    )
    return counts.sort_values(["platform", "rejection_reason"])


def build_instrument_audit(rows, expected):
    rows["is_valid"] = rows["rejection_reason"].eq("valid")
    rows["is_crossed"] = rows["rejection_reason"].eq("crossed_book")
    rows["is_missing_side"] = rows["rejection_reason"].isin(
        [
            "missing_best_bid",
            "missing_best_ask",
            "nonpositive_best_bid",
            "nonpositive_best_ask",
        ]
    )
    rows["is_invalid_price"] = rows["rejection_reason"].isin(
        ["best_bid_above_one", "best_ask_above_one"]
    )

    observed = (
        rows.groupby(
            ["platform", "game_id", "team", "instrument_id"],
            dropna=False,
        )
        .agg(
            input_rows=("rejection_reason", "size"),
            valid_rows=("is_valid", "sum"),
            crossed_rows=("is_crossed", "sum"),
            missing_side_rows=("is_missing_side", "sum"),
            invalid_price_rows=("is_invalid_price", "sum"),
            first_timestamp=("parsed_timestamp", "min"),
            last_timestamp=("parsed_timestamp", "max"),
            median_best_bid=("best_bid", "median"),
            median_best_ask=("best_ask", "median"),
        )
        .reset_index()
    )

    if expected.empty:
        result = observed
        result["expected_in_market_map"] = pd.NA
    else:
        expected_keys = expected[
            ["platform", "game_id", "instrument_id"]
        ].drop_duplicates()
        expected_keys["expected_in_market_map"] = True
        result = expected.merge(
            observed,
            on=["platform", "game_id", "instrument_id"],
            how="outer",
            suffixes=("_expected", "_observed"),
        )
        result = result.merge(
            expected_keys,
            on=["platform", "game_id", "instrument_id"],
            how="left",
        )
        if "team_expected" in result.columns or "team_observed" in result.columns:
            expected_team = result.get(
                "team_expected", pd.Series(pd.NA, index=result.index)
            )
            observed_team = result.get(
                "team_observed", pd.Series(pd.NA, index=result.index)
            )
            result["team"] = expected_team.fillna(observed_team)
            result = result.drop(
                columns=["team_expected", "team_observed"], errors="ignore"
            )

    numeric_columns = [
        "input_rows",
        "valid_rows",
        "crossed_rows",
        "missing_side_rows",
        "invalid_price_rows",
    ]
    for column in numeric_columns:
        if column not in result.columns:
            result[column] = 0
        result[column] = result[column].fillna(0).astype("int64")

    result["absent_from_filtered_input"] = result["input_rows"].eq(0)
    result["present_but_no_valid_rows"] = result["input_rows"].gt(0) & result[
        "valid_rows"
    ].eq(0)
    return result.sort_values(["platform", "game_id", "instrument_id"])


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    platform_rows = []
    samples = []

    for platform, path in BBO_PATHS.items():
        print(f"Auditing {platform}: {path}")
        rows, platform_samples = audit_platform(platform, path)
        platform_rows.append(rows)
        samples.extend(platform_samples)

    rows = pd.concat(platform_rows, ignore_index=True)
    reason_counts = build_reason_counts(rows)
    expected = load_expected_instruments()
    instrument_audit = build_instrument_audit(rows, expected)

    reason_counts.to_csv(REASON_OUTPUT, index=False)
    instrument_audit.to_csv(INSTRUMENT_OUTPUT, index=False)

    if samples:
        pd.concat(samples, ignore_index=True).to_csv(SAMPLE_OUTPUT, index=False)
    else:
        pd.DataFrame(columns=["platform", "rejection_reason"]).to_csv(
            SAMPLE_OUTPUT, index=False
        )

    print()
    print(reason_counts.to_string(index=False))
    print()
    print(
        "Expected instruments absent from filtered input:",
        int(instrument_audit["absent_from_filtered_input"].sum()),
    )
    print(
        "Instruments present but with no valid rows:",
        int(instrument_audit["present_but_no_valid_rows"].sum()),
    )
    problem_instruments = instrument_audit[
        instrument_audit["absent_from_filtered_input"]
        | instrument_audit["present_but_no_valid_rows"]
    ]
    if not problem_instruments.empty:
        print()
        print("Missing or fully rejected instruments:")
        print(
            problem_instruments[
                [
                    "platform",
                    "game_id",
                    "team",
                    "instrument_id",
                    "input_rows",
                    "valid_rows",
                    "crossed_rows",
                    "missing_side_rows",
                    "invalid_price_rows",
                    "absent_from_filtered_input",
                    "present_but_no_valid_rows",
                ]
            ].to_string(index=False)
        )

    offenders = instrument_audit[
        instrument_audit["crossed_rows"].gt(0)
        | instrument_audit["missing_side_rows"].gt(0)
    ].copy()
    if not offenders.empty:
        offenders["problem_rows"] = (
            offenders["crossed_rows"] + offenders["missing_side_rows"]
        )
        offenders = offenders.nlargest(20, "problem_rows")
        print()
        print("Top 20 instruments with crossed or missing-side rows:")
        print(
            offenders[
                [
                    "platform",
                    "game_id",
                    "team",
                    "instrument_id",
                    "input_rows",
                    "valid_rows",
                    "crossed_rows",
                    "missing_side_rows",
                ]
            ].to_string(index=False)
        )
    print(f"Saved: {REASON_OUTPUT}")
    print(f"Saved: {INSTRUMENT_OUTPUT}")
    print(f"Saved: {SAMPLE_OUTPUT}")


if __name__ == "__main__":
    main()