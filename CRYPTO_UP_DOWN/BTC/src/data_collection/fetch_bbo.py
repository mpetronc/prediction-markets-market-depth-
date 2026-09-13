"""Fetch free historical orderbook snapshots, or quote paid Predexon tick data."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from _pipeline_common import (
    ASSET_ROOT,
    PredexonClient,
    RunLog,
    epoch_milliseconds,
    in_incident_window,
    read_csv,
    run_logged,
    safe_filename,
    treatment_period,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
)


MAP_PATH = ASSET_ROOT / "data/matched_markets/BTC_market_map.csv"
PART_ROOT = ASSET_ROOT / "data/processed/_parts/bbo"
KALSHI_PART_ROOT = ASSET_ROOT / "data/processed/_parts/bbo_subcent/kalshi"
RAW_ROOT = ASSET_ROOT / "data/raw"
KALSHI_OUT = ASSET_ROOT / "data/processed/kalshi/bbo.csv.gz"
POLY_PRE_OUT = ASSET_ROOT / "data/processed/polymarket/bbo_pre.csv.gz"
POLY_POST_OUT = ASSET_ROOT / "data/processed/polymarket/bbo_post.csv.gz"
MANIFEST_PATH = ASSET_ROOT / "data/processed/bbo_collection_manifest.json"
QUOTE_PATH = ASSET_ROOT / "data/results/tick_data_quotes.csv"
SCHEMA_VERSION = "2026-09-04.1"

FIELDS = [
    "schema_version",
    "pair_id",
    "asset",
    "family",
    "platform",
    "instrument_id",
    "condition_id",
    "outcome",
    "timestamp_ms",
    "indexed_at_ms",
    "observation_period",
    "treatment_received",
    "taker_delay_ms",
    "incident_window",
    "best_bid",
    "best_ask",
    "canonical_yes_best_bid",
    "canonical_yes_best_ask",
    "mid_price",
    "spread",
    "bid_depth_contracts",
    "ask_depth_contracts",
    "bid_depth_notional",
    "ask_depth_notional",
    "book_hash",
    "sequence",
    "capture_source",
    "tick_size",
    "bids_json",
    "asks_json",
]


def as_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalized_levels(levels: object, prices_are_cents: bool) -> list[dict]:
    result: list[dict] = []
    for level in levels if isinstance(levels, list) else []:
        price = as_float(level.get("price")) if isinstance(level, dict) else None
        size = as_float(level.get("size")) if isinstance(level, dict) else None
        if price is None or size is None:
            continue
        if prices_are_cents:
            price /= 100
        # Predexon/Kalshi may expose 0/100 boundary placeholders. They are not
        # executable binary-contract price levels and must not define BBO.
        if not 0 < price < 1:
            continue
        result.append({"price": price, "size": size})
    return result


def level_metrics(levels: list[dict]) -> tuple[float, float]:
    contracts = sum(level["size"] for level in levels)
    notional = sum(level["price"] * level["size"] for level in levels)
    return contracts, notional


def normalize_snapshot(
    pair: dict[str, str],
    platform: str,
    outcome: str,
    instrument_id: str,
    snapshot: dict,
) -> dict:
    prices_are_cents = platform == "kalshi"
    bids = normalized_levels(
        snapshot.get("yes_bids") if prices_are_cents else snapshot.get("bids"),
        prices_are_cents,
    )
    asks = normalized_levels(
        snapshot.get("yes_asks") if prices_are_cents else snapshot.get("asks"),
        prices_are_cents,
    )
    best_bid = max((level["price"] for level in bids), default=None)
    best_ask = min((level["price"] for level in asks), default=None)

    if outcome.lower() in {"no", "down"}:
        canonical_bid = 1 - best_ask if best_ask is not None else None
        canonical_ask = 1 - best_bid if best_bid is not None else None
    else:
        canonical_bid = best_bid
        canonical_ask = best_ask

    bid_contracts, bid_notional = level_metrics(bids)
    ask_contracts, ask_notional = level_metrics(asks)
    timestamp_ms = int(snapshot["timestamp"])
    timestamp_seconds = timestamp_ms / 1_000
    period = treatment_period(timestamp_seconds)
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair["pair_id"],
        "asset": pair["asset"],
        "family": pair["family"],
        "platform": platform,
        "instrument_id": instrument_id,
        "condition_id": pair["polymarket_condition_id"] if platform == "polymarket" else "",
        "outcome": outcome,
        "timestamp_ms": timestamp_ms,
        "indexed_at_ms": snapshot.get("indexedAt") or "",
        "observation_period": period,
        "treatment_received": platform == "polymarket",
        "taker_delay_ms": (250 if period == "pre" else 50) if platform == "polymarket" else 0,
        "incident_window": platform == "polymarket" and in_incident_window(timestamp_seconds),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "canonical_yes_best_bid": canonical_bid,
        "canonical_yes_best_ask": canonical_ask,
        "mid_price": (
            (best_bid + best_ask) / 2
            if best_bid is not None and best_ask is not None
            else None
        ),
        "spread": (
            best_ask - best_bid
            if best_bid is not None and best_ask is not None
            else None
        ),
        "bid_depth_contracts": bid_contracts,
        "ask_depth_contracts": ask_contracts,
        "bid_depth_notional": bid_notional,
        "ask_depth_notional": ask_notional,
        "book_hash": snapshot.get("hash") or "",
        "sequence": snapshot.get("sequence") or "",
        "capture_source": snapshot.get("source") or "",
        "tick_size": snapshot.get("tickSize") or "",
        "bids_json": json.dumps(bids, separators=(",", ":")),
        "asks_json": json.dumps(asks, separators=(",", ":")),
    }


def write_part(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def collect_instrument(
    client: PredexonClient,
    pair: dict[str, str],
    platform: str,
    outcome: str,
    instrument_id: str,
    overwrite: bool,
) -> tuple[Path, int, int, bool]:
    suffix = f"_{outcome.lower()}" if platform == "polymarket" else ""
    platform_part_root = (
        KALSHI_PART_ROOT if platform == "kalshi" else PART_ROOT / platform
    )
    part_path = platform_part_root / f"{safe_filename(pair['pair_id'])}{suffix}.csv.gz"
    if part_path.exists() and not overwrite:
        with gzip.open(part_path, "rt", newline="", encoding="utf-8") as handle:
            return part_path, sum(1 for _ in csv.DictReader(handle)), 0, True

    start_ms = epoch_milliseconds(pair["comparison_start_utc"])
    end_ms = epoch_milliseconds(pair["comparison_end_utc"])
    if platform == "kalshi":
        endpoint = "kalshi/orderbooks-subcent"
        params = {
            "ticker": instrument_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 2_000,
            "sources": "spine",
        }
    else:
        endpoint = "polymarket/orderbooks"
        params = {
            "token_id": instrument_id,
            "start_time": start_ms,
            "end_time": end_ms,
            "limit": 200,
        }

    rows: list[dict] = []
    pages = 0
    raw_dir = (
        RAW_ROOT
        / platform
        / ("orderbooks_subcent" if platform == "kalshi" else "orderbooks")
        / f"{safe_filename(pair['pair_id'])}{suffix}"
    )
    for page_number, payload in client.cursor_pages(endpoint, params, "snapshots"):
        pages = page_number
        write_json_gzip_atomic(
            payload, raw_dir / f"page_{page_number:05d}.json.gz"
        )
        for snapshot in payload["snapshots"]:
            row = normalize_snapshot(
                pair, platform, outcome, instrument_id, snapshot
            )
            if start_ms <= int(row["timestamp_ms"]) < end_ms:
                rows.append(row)

    unique: dict[tuple, dict] = {}
    for row in rows:
        marker = (
            row["instrument_id"],
            row["timestamp_ms"],
            row["sequence"] or row["book_hash"],
        )
        unique[marker] = row
    rows = sorted(unique.values(), key=lambda row: int(row["timestamp_ms"]))
    write_part(rows, part_path)
    return part_path, len(rows), pages, False


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


def quote_ticks(
    client: PredexonClient,
    pairs: list[dict[str, str]],
    venues: list[str],
    run_log: RunLog,
) -> None:
    quotes: list[dict] = []
    completed = 0
    total = len(pairs) * len(venues)
    for pair in pairs:
        for platform in venues:
            market_id = (
                pair["kalshi_ticker"]
                if platform == "kalshi"
                else pair["polymarket_condition_id"]
            )
            params = {
                "venue": platform,
                "start_time": epoch_milliseconds(pair["comparison_start_utc"]),
                "end_time": epoch_milliseconds(pair["comparison_end_utc"]),
                "market_id": market_id,
            }
            try:
                payload = client.get_json("data/ticks/quote", params)
                quotes.append(
                    {
                        "pair_id": pair["pair_id"],
                        "platform": platform,
                        "market_id": market_id,
                        "status": "quoted",
                        "total_rows": payload.get("total_rows"),
                        "total_bytes": payload.get("total_bytes"),
                        "price_cents": payload.get("price_cents"),
                        "currency": payload.get("currency"),
                        "balance_cents": payload.get("balance_cents"),
                        "quote_id": payload.get("quote_id"),
                        "expires_at": payload.get("expires_at"),
                        "error": "",
                    }
                )
                run_log.info(
                    f"Tick quote pair={pair['pair_id']} platform={platform} "
                    f"rows={payload.get('total_rows')} bytes={payload.get('total_bytes')} "
                    f"price_cents={payload.get('price_cents')}"
                )
            except RuntimeError as exc:
                quotes.append(
                    {
                        "pair_id": pair["pair_id"],
                        "platform": platform,
                        "market_id": market_id,
                        "status": "unavailable",
                        "error": str(exc),
                    }
                )
                run_log.warning(
                    f"Tick quote unavailable pair={pair['pair_id']} "
                    f"platform={platform} error={exc}"
                )
            completed += 1
            run_log.progress(completed, total, "mode=no_charge_quote")
    quote_fields = [
        "pair_id",
        "platform",
        "market_id",
        "status",
        "total_rows",
        "total_bytes",
        "price_cents",
        "currency",
        "balance_cents",
        "quote_id",
        "expires_at",
        "error",
    ]
    write_csv_atomic(quotes, QUOTE_PATH, quote_fields)
    run_log.info(
        f"Tick quote output quotes={len(quotes)} requests={client.request_count} "
        f"retries={client.retry_count} file={QUOTE_PATH} paid_download_requested=false"
    )


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=["both", "kalshi", "polymarket"], default="both")
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--overwrite-parts", action="store_true")
    parser.add_argument(
        "--quote-ticks",
        action="store_true",
        help="Request no-charge tick-data quotes instead of snapshot history.",
    )
    args = parser.parse_args()

    pairs = read_csv(MAP_PATH)
    if args.max_markets > 0:
        pairs = pairs[: args.max_markets]
    venues = ["kalshi", "polymarket"] if args.venue == "both" else [args.venue]
    client = PredexonClient(request_interval=args.request_interval, run_log=run_log)
    run_log.info(
        f"Orderbook collection configured pairs={len(pairs)} venues={','.join(venues)} "
        f"partial_run={args.max_markets > 0} overwrite_parts={args.overwrite_parts} "
        f"quote_only={args.quote_ticks} market_map={MAP_PATH}"
    )

    if args.quote_ticks:
        run_log.info("No-charge quote mode enabled; paid tick download endpoint is not called")
        quote_ticks(client, pairs, venues, run_log)
        return

    run_log.info(
        "Using Predexon's free snapshot-history endpoints. "
        "Kalshi uses the exact sub-cent endpoint with spine capture sources. "
        "Sparse Polymarket coverage is recorded as a quality result; "
        "paid tick data is never requested by this script."
    )
    parts: dict[str, list[Path]] = {"kalshi": [], "polymarket": []}
    rows_by_venue = {"kalshi": 0, "polymarket": 0}
    empty_parts = {"kalshi": 0, "polymarket": 0}
    pages_by_venue = {"kalshi": 0, "polymarket": 0}
    reused_parts = {"kalshi": 0, "polymarket": 0}

    for index, pair in enumerate(pairs, start=1):
        pair_details: list[str] = []
        if "kalshi" in venues:
            part, count, pages, reused = collect_instrument(
                client,
                pair,
                "kalshi",
                "Yes",
                pair["kalshi_ticker"],
                args.overwrite_parts,
            )
            parts["kalshi"].append(part)
            rows_by_venue["kalshi"] += count
            empty_parts["kalshi"] += int(count == 0)
            pages_by_venue["kalshi"] += pages
            reused_parts["kalshi"] += int(reused)
            pair_details.append(
                f"kalshi_rows={count} kalshi_pages={pages} "
                f"kalshi_source={'reused' if reused else 'fetched'}"
            )

        if "polymarket" in venues:
            for outcome, token_field in [
                ("Up", "polymarket_up_token_id"),
                ("Down", "polymarket_down_token_id"),
            ]:
                part, count, pages, reused = collect_instrument(
                    client,
                    pair,
                    "polymarket",
                    outcome,
                    pair[token_field],
                    args.overwrite_parts,
                )
                parts["polymarket"].append(part)
                rows_by_venue["polymarket"] += count
                empty_parts["polymarket"] += int(count == 0)
                pages_by_venue["polymarket"] += pages
                reused_parts["polymarket"] += int(reused)
                label = outcome.lower()
                pair_details.append(
                    f"polymarket_{label}_rows={count} polymarket_{label}_pages={pages} "
                    f"polymarket_{label}_source={'reused' if reused else 'fetched'}"
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
            "source_by_venue": {
                "kalshi": "predexon_free_orderbooks_subcent_spine",
                "polymarket": "predexon_free_orderbooks",
            },
            "paid_tick_data_requested": False,
            "market_map": str(MAP_PATH),
            "pairs_selected": len(pairs),
            "venues": venues,
            "partial_run": args.max_markets > 0,
            "rows_by_venue_before_period_split": rows_by_venue,
            "api_pages_by_venue": pages_by_venue,
            "reused_parts": reused_parts,
            "empty_instruments": empty_parts,
            "output_rows": output_counts,
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Orderbook collection output rows={output_counts} pages={pages_by_venue} "
        f"reused_parts={reused_parts} empty_instruments={empty_parts} "
        f"requests={client.request_count} retries={client.retry_count} "
        f"kalshi_file={KALSHI_OUT} polymarket_pre_file={POLY_PRE_OUT} "
        f"polymarket_post_file={POLY_POST_OUT} manifest={MANIFEST_PATH}"
    )
    if empty_parts["polymarket"]:
        run_log.warning(
            "WARNING: one or more Polymarket token histories were empty. "
            "Use --quote-ticks to price the full-resolution alternative."
        )


if __name__ == "__main__":
    run_logged("fetch_bbo", main)
