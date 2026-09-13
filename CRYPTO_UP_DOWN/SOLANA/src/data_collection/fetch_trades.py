"""Fetch matched SOL trades from Predexon with resumable per-market parts."""

from __future__ import annotations

import argparse
import csv
import gzip
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from _pipeline_common import (
    ASSET_ROOT,
    PredexonClient,
    RunLog,
    epoch_seconds,
    in_incident_window,
    read_csv,
    run_logged,
    safe_filename,
    treatment_period,
    write_json_atomic,
    write_json_gzip_atomic,
)


MAP_PATH = ASSET_ROOT / "data/matched_markets/SOL_market_map.csv"
PART_ROOT = ASSET_ROOT / "data/processed/_parts/trades"
RAW_ROOT = ASSET_ROOT / "data/raw"
KALSHI_OUT = ASSET_ROOT / "data/processed/kalshi/trades.csv.gz"
POLY_PRE_OUT = ASSET_ROOT / "data/processed/polymarket/trades_pre.csv.gz"
POLY_POST_OUT = ASSET_ROOT / "data/processed/polymarket/trades_post.csv.gz"
MANIFEST_PATH = ASSET_ROOT / "data/processed/trades_collection_manifest.json"
SCHEMA_VERSION = "2026-09-04.1"

FIELDS = [
    "schema_version",
    "pair_id",
    "asset",
    "family",
    "platform",
    "instrument_id",
    "condition_id",
    "token_id",
    "outcome",
    "trade_id",
    "timestamp",
    "timestamp_ms",
    "observation_period",
    "treatment_received",
    "taker_delay_ms",
    "incident_window",
    "price",
    "canonical_yes_price",
    "size",
    "size_source",
    "size_is_exact",
    "notional_usd",
    "reported_side",
    "taker_side",
    "tx_hash",
    "order_hash",
    "user",
    "taker",
    "fee_usd",
    "exchange_version",
    "builder",
    "metadata",
]


def as_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_decimal(value: object) -> Decimal | None:
    """Parse API fixed-point values without losing decimal precision."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def kalshi_trade_size(trade: dict) -> tuple[Decimal | None, str, bool]:
    """Prefer Kalshi's exact fixed-point quantity, with an auditable fallback."""
    count_fp = as_decimal(trade.get("count_fp"))
    if count_fp is not None:
        return count_fp, "count_fp", True

    count = as_decimal(trade.get("count"))
    if count is not None:
        # Predexon's current `count` field is rounded to a whole contract.
        return count, "count", False

    return None, "missing", False


def write_part(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def normalize_kalshi(pair: dict[str, str], trade: dict) -> dict:
    timestamp = int(trade["created_time"])
    yes_price = as_float(trade.get("yes_price"))
    yes_price_decimal = as_decimal(trade.get("yes_price"))
    size, size_source, size_is_exact = kalshi_trade_size(trade)
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "asset": pair["asset"],
        "family": pair["family"],
        "platform": "kalshi",
        "instrument_id": pair["kalshi_ticker"],
        "condition_id": "",
        "token_id": "",
        "outcome": "Yes",
        "trade_id": trade.get("trade_id"),
        "timestamp": timestamp,
        "timestamp_ms": trade.get("created_time_ms") or timestamp * 1_000,
        "observation_period": treatment_period(timestamp),
        "treatment_received": False,
        "taker_delay_ms": 0,
        "incident_window": False,
        "price": yes_price,
        "canonical_yes_price": yes_price,
        "size": size,
        "size_source": size_source,
        "size_is_exact": size_is_exact,
        "notional_usd": (
            yes_price_decimal * size
            if yes_price_decimal is not None and size is not None
            else None
        ),
        "reported_side": "",
        "taker_side": trade.get("taker_side"),
        "tx_hash": "",
        "order_hash": "",
        "user": "",
        "taker": "",
        "fee_usd": "",
        "exchange_version": "",
        "builder": "",
        "metadata": "",
    }


def normalize_polymarket(pair: dict[str, str], trade: dict) -> dict:
    timestamp = int(trade["timestamp"])
    price = as_float(trade.get("price"))
    size = as_float(trade.get("shares_normalized"))
    size_source = "shares_normalized"
    if size is None:
        raw_size = as_float(trade.get("shares"))
        size = raw_size / 1_000_000 if raw_size is not None else None
        size_source = "shares_scaled_1e6" if size is not None else "missing"
    outcome = str(trade.get("outcome_label") or "")
    is_yes_side = trade.get("is_yes_side")
    if is_yes_side is True or outcome.lower() in {"yes", "up", "above"}:
        canonical_price = price
    elif is_yes_side is False or outcome.lower() in {"no", "down", "below"}:
        canonical_price = 1 - price if price is not None else None
    else:
        canonical_price = None
    period = treatment_period(timestamp)
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "asset": pair["asset"],
        "family": pair["family"],
        "platform": "polymarket",
        "instrument_id": trade.get("token_id"),
        "condition_id": pair["polymarket_condition_id"],
        "token_id": trade.get("token_id"),
        "outcome": outcome,
        "trade_id": "",
        "timestamp": timestamp,
        "timestamp_ms": timestamp * 1_000,
        "observation_period": period,
        "treatment_received": True,
        "taker_delay_ms": 250 if period == "pre" else 50,
        "incident_window": in_incident_window(timestamp),
        "price": price,
        "canonical_yes_price": canonical_price,
        "size": size,
        "size_source": size_source,
        "size_is_exact": "",
        "notional_usd": as_float(trade.get("amount_usd")),
        # Predexon does not define this REST field clearly enough to relabel it
        # as aggressor side, so preserve it exactly as reported.
        "reported_side": trade.get("side"),
        "taker_side": "",
        "tx_hash": trade.get("tx_hash"),
        "order_hash": trade.get("order_hash"),
        "user": trade.get("user"),
        "taker": trade.get("taker"),
        "fee_usd": trade.get("fee_usd"),
        "exchange_version": trade.get("exchange_version"),
        "builder": trade.get("builder"),
        "metadata": trade.get("metadata"),
    }


def collect_instrument(
    client: PredexonClient,
    pair: dict[str, str],
    platform: str,
    overwrite: bool,
) -> tuple[Path, int, int, bool, dict[str, int]]:
    identifier = (
        pair["kalshi_ticker"]
        if platform == "kalshi"
        else pair["polymarket_condition_id"]
    )
    part_path = PART_ROOT / platform / f"{safe_filename(pair['pair_id'])}.csv.gz"
    if part_path.exists() and not overwrite:
        with gzip.open(part_path, "rt", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            sources: Counter[str] = Counter()
            row_count = 0
            for row in reader:
                row_count += 1
                sources[row.get("size_source") or "legacy_unknown"] += 1
            return part_path, row_count, 0, True, dict(sources)

    start = epoch_seconds(pair["comparison_start_utc"])
    end = epoch_seconds(pair["comparison_end_utc"])
    if platform == "kalshi":
        endpoint = "kalshi/trades"
        params = {
            "ticker": identifier,
            "start_time": start,
            "end_time": end,
            "limit": 500,
            "order": "asc",
        }
    else:
        endpoint = "polymarket/trades"
        params = {
            "condition_id": identifier,
            "start_time": start,
            "end_time": end,
            "limit": 500,
            "order": "asc",
        }

    rows: list[dict] = []
    pages = 0
    raw_dir = RAW_ROOT / platform / "trades" / safe_filename(pair["pair_id"])
    for page_number, payload in client.cursor_pages(endpoint, params, "trades"):
        pages = page_number
        write_json_gzip_atomic(
            payload, raw_dir / f"page_{page_number:05d}.json.gz"
        )
        normalizer = normalize_kalshi if platform == "kalshi" else normalize_polymarket
        for trade in payload["trades"]:
            row = normalizer(pair, trade)
            if start <= int(row["timestamp"]) < end:
                rows.append(row)

    if rows:
        dedupe_fields = (
            ["trade_id"]
            if platform == "kalshi"
            else ["tx_hash", "order_hash", "token_id", "user", "size", "price"]
        )
        unique: dict[tuple, dict] = {}
        for row in rows:
            unique[tuple(row[field] for field in dedupe_fields)] = row
        rows = sorted(unique.values(), key=lambda row: (int(row["timestamp_ms"]), str(row["trade_id"])))

    write_part(rows, part_path)
    sources = Counter(str(row.get("size_source") or "missing") for row in rows)
    return part_path, len(rows), pages, False, dict(sources)


def combine_parts(part_paths: list[Path], output: Path, period: str | None) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    count = 0
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS)
        writer.writeheader()
        for part in sorted(part_paths):
            with gzip.open(part, "rt", newline="", encoding="utf-8") as source:
                for row in csv.DictReader(source):
                    if period is None or row["observation_period"] == period:
                        writer.writerow(row)
                        count += 1
    temporary.replace(output)
    return count


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=["both", "kalshi", "polymarket"], default="both")
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--overwrite-parts", action="store_true")
    args = parser.parse_args()

    pairs = read_csv(MAP_PATH)
    if args.max_markets > 0:
        pairs = pairs[: args.max_markets]
    venues = ["kalshi", "polymarket"] if args.venue == "both" else [args.venue]
    client = PredexonClient(request_interval=args.request_interval, run_log=run_log)
    parts: dict[str, list[Path]] = {"kalshi": [], "polymarket": []}
    rows_by_venue = {"kalshi": 0, "polymarket": 0}
    empty_parts = {"kalshi": 0, "polymarket": 0}
    pages_by_venue = {"kalshi": 0, "polymarket": 0}
    reused_parts = {"kalshi": 0, "polymarket": 0}
    size_sources_by_venue = {"kalshi": Counter(), "polymarket": Counter()}
    run_log.info(
        f"Trade collection configured pairs={len(pairs)} venues={','.join(venues)} "
        f"partial_run={args.max_markets > 0} overwrite_parts={args.overwrite_parts} "
        f"market_map={MAP_PATH}"
    )

    for index, pair in enumerate(pairs, start=1):
        pair_details: list[str] = []
        for platform in venues:
            part, count, pages, reused, size_sources = collect_instrument(
                client, pair, platform, args.overwrite_parts
            )
            parts[platform].append(part)
            rows_by_venue[platform] += count
            empty_parts[platform] += int(count == 0)
            pages_by_venue[platform] += pages
            reused_parts[platform] += int(reused)
            size_sources_by_venue[platform].update(size_sources)
            pair_details.append(
                f"{platform}_rows={count} {platform}_pages={pages} "
                f"{platform}_source={'reused' if reused else 'fetched'}"
            )
        run_log.progress(
            index,
            len(pairs),
            f"pair={pair['pair_id']} {' '.join(pair_details)}",
        )

    output_counts: dict[str, int] = {}
    if "kalshi" in venues:
        output_counts["kalshi"] = combine_parts(parts["kalshi"], KALSHI_OUT, None)
    if "polymarket" in venues:
        output_counts["polymarket_pre"] = combine_parts(
            parts["polymarket"], POLY_PRE_OUT, "pre"
        )
        output_counts["polymarket_post"] = combine_parts(
            parts["polymarket"], POLY_POST_OUT, "post"
        )

    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "market_map": str(MAP_PATH),
            "pairs_selected": len(pairs),
            "venues": venues,
            "partial_run": args.max_markets > 0,
            "rows_by_venue_before_period_split": rows_by_venue,
            "api_pages_by_venue": pages_by_venue,
            "reused_parts": reused_parts,
            "empty_instruments": empty_parts,
            "size_sources_by_venue": {
                venue: dict(counts) for venue, counts in size_sources_by_venue.items()
            },
            "output_rows": output_counts,
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Trade collection output rows={output_counts} pages={pages_by_venue} "
        f"reused_parts={reused_parts} empty_instruments={empty_parts} "
        f"size_sources_by_venue={dict((venue, dict(counts)) for venue, counts in size_sources_by_venue.items())} "
        f"requests={client.request_count} retries={client.retry_count} "
        f"kalshi_file={KALSHI_OUT} polymarket_pre_file={POLY_PRE_OUT} "
        f"polymarket_post_file={POLY_POST_OUT} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("fetch_trades", main)
