from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

ORIGINAL_BBO_PATH = ROOT / "data/processed/kalshi/bbo.csv"
FILTERED_BBO_PATH = (
    ROOT / "data/processed/kalshi/filtered_bbo_trades/bbo_filtered.csv"
)
ORIGINAL_TRADES_PATH = ROOT / "data/processed/kalshi/trades.csv"
FILTERED_TRADES_PATH = (
    ROOT / "data/processed/kalshi/filtered_bbo_trades/trades_filtered.csv"
)
AUDIT_PATH = ROOT / "data/results/bbo_audit_by_instrument.csv"
OUTPUT_PATH = ROOT / "data/results/missing_bbo_instrument_source_check.csv"

CHUNK_SIZE = 200_000


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


def require_file(path):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def load_targets():
    require_file(AUDIT_PATH)
    audit = pd.read_csv(
        AUDIT_PATH,
        dtype={
            "platform": "string",
            "game_id": "string",
            "team": "string",
            "instrument_id": "string",
        },
    )

    required = {
        "platform",
        "game_id",
        "team",
        "instrument_id",
        "absent_from_filtered_input",
    }
    missing = required - set(audit.columns)
    if missing:
        raise ValueError(f"Audit file is missing columns: {sorted(missing)}")

    absent = (
        audit["absent_from_filtered_input"]
        .astype("string")
        .str.lower()
        .eq("true")
    )
    targets = audit[audit["platform"].eq("kalshi") & absent].copy()
    targets["instrument_id"] = targets["instrument_id"].str.strip()
    targets = targets[
        ["platform", "game_id", "team", "instrument_id"]
    ].drop_duplicates()

    if targets.empty:
        raise ValueError("No absent Kalshi instruments were found in the audit file")

    return targets


def scan_file(path, target_ids, prefix):
    require_file(path)
    header = pd.read_csv(path, nrows=0)
    required = {"instrument_id", "timestamp"}
    missing = required - set(header.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")

    parts = []

    for chunk in pd.read_csv(
        path,
        usecols=["instrument_id", "timestamp"],
        chunksize=CHUNK_SIZE,
        low_memory=False,
        dtype={"instrument_id": "string"},
    ):
        chunk["instrument_id"] = chunk["instrument_id"].str.strip()
        chunk = chunk[chunk["instrument_id"].isin(target_ids)].copy()
        if chunk.empty:
            continue
        chunk["parsed_timestamp"] = parse_timestamps(chunk["timestamp"])
        parts.append(
            chunk.groupby("instrument_id", dropna=False).agg(
                rows=("timestamp", "size"),
                valid_timestamp_rows=("parsed_timestamp", "count"),
                first_timestamp=("parsed_timestamp", "min"),
                last_timestamp=("parsed_timestamp", "max"),
            )
        )

    columns = [
        f"{prefix}_rows",
        f"{prefix}_valid_timestamp_rows",
        f"{prefix}_first_timestamp",
        f"{prefix}_last_timestamp",
    ]

    if not parts:
        return pd.DataFrame(columns=columns).rename_axis("instrument_id")

    combined = pd.concat(parts).reset_index()
    result = combined.groupby("instrument_id", dropna=False).agg(
        rows=("rows", "sum"),
        valid_timestamp_rows=("valid_timestamp_rows", "sum"),
        first_timestamp=("first_timestamp", "min"),
        last_timestamp=("last_timestamp", "max"),
    )
    result.columns = columns
    return result


def classify(row):
    if row["original_bbo_rows"] == 0:
        return "absent_from_original_bbo"
    if row["original_bbo_valid_timestamp_rows"] == 0:
        return "original_rows_have_invalid_timestamps"
    if row["filtered_bbo_rows"] == 0:
        return "present_original_but_removed_by_time_filter"
    return "present_in_filtered_bbo"


def classify_evidence(row):
    if row["filtered_trade_rows"] > 0 and row["original_bbo_rows"] == 0:
        return "in_game_trades_confirm_ticker_but_original_bbo_is_absent"
    if row["filtered_trade_rows"] > 0 and row["filtered_bbo_rows"] == 0:
        return "in_game_trades_exist_but_in_game_bbo_is_absent"
    if row["original_trade_rows"] > 0 and row["filtered_trade_rows"] == 0:
        return "ticker_has_trades_but_none_inside_game_window"
    if row["original_trade_rows"] == 0:
        return "ticker_not_found_in_trade_data"
    return "bbo_and_trades_present_inside_game_window"


def main():
    targets = load_targets()
    target_ids = set(targets["instrument_id"].dropna())

    print(f"Checking {len(target_ids)} absent Kalshi instruments")
    print(f"Original BBO: {ORIGINAL_BBO_PATH}")
    print(f"Filtered BBO: {FILTERED_BBO_PATH}")
    print(f"Original trades: {ORIGINAL_TRADES_PATH}")
    print(f"Filtered trades: {FILTERED_TRADES_PATH}")

    original_bbo = scan_file(ORIGINAL_BBO_PATH, target_ids, "original_bbo")
    filtered_bbo = scan_file(FILTERED_BBO_PATH, target_ids, "filtered_bbo")
    original_trades = scan_file(
        ORIGINAL_TRADES_PATH, target_ids, "original_trade"
    )
    filtered_trades = scan_file(
        FILTERED_TRADES_PATH, target_ids, "filtered_trade"
    )

    result = targets.merge(original_bbo, on="instrument_id", how="left")
    result = result.merge(filtered_bbo, on="instrument_id", how="left")
    result = result.merge(original_trades, on="instrument_id", how="left")
    result = result.merge(filtered_trades, on="instrument_id", how="left")

    count_columns = [
        "original_bbo_rows",
        "original_bbo_valid_timestamp_rows",
        "filtered_bbo_rows",
        "filtered_bbo_valid_timestamp_rows",
        "original_trade_rows",
        "original_trade_valid_timestamp_rows",
        "filtered_trade_rows",
        "filtered_trade_valid_timestamp_rows",
    ]
    for column in count_columns:
        result[column] = (
            pd.to_numeric(result[column], errors="coerce")
            .fillna(0)
            .astype("int64")
        )

    result["classification"] = result.apply(classify, axis=1)
    result["trade_evidence"] = result.apply(classify_evidence, axis=1)
    result = result.sort_values(["game_id", "instrument_id"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_PATH, index=False)

    print()
    print(
        result[
            [
                "game_id",
                "team",
                "instrument_id",
                "original_bbo_rows",
                "original_bbo_first_timestamp",
                "original_bbo_last_timestamp",
                "filtered_bbo_rows",
                "original_trade_rows",
                "filtered_trade_rows",
                "filtered_trade_first_timestamp",
                "filtered_trade_last_timestamp",
                "classification",
                "trade_evidence",
            ]
        ].to_string(index=False)
    )
    print()
    print("Classification counts:")
    print(result["classification"].value_counts().to_string())
    print()
    print("Trade evidence counts:")
    print(result["trade_evidence"].value_counts().to_string())
    print(f"Saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()