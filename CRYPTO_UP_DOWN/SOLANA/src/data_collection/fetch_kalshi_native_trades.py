"""Fetch exact SOL 15-minute Kalshi trades from Kalshi's public API.

This is a corrective, Kalshi-only collection stage. It does not call Predexon,
does not touch Polymarket data, and does not overwrite the existing rounded
Predexon trade file. Native responses and resumable per-market CSV parts are
kept separately. The final output contains ``count_fp`` as ``size`` and can be
promoted to the canonical Kalshi input only after the reconciliation report is
reviewed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from _kalshi_public import BASE_URL, KalshiPublicClient
from _pipeline_common import (
    ASSET_ROOT,
    RunLog,
    epoch_seconds,
    parse_utc,
    read_csv,
    run_logged,
    safe_filename,
    treatment_period,
    write_csv_atomic,
    write_json_atomic,
    write_json_gzip_atomic,
)
from fetch_trades import FIELDS


MAP_PATH = ASSET_ROOT / "data/matched_markets/SOL_market_map.csv"
NATIVE_PART_ROOT = ASSET_ROOT / "data/processed/_parts/trades_native/kalshi"
PREDEXON_PART_ROOT = ASSET_ROOT / "data/processed/_parts/trades/kalshi"
RAW_ROOT = ASSET_ROOT / "data/raw/kalshi_native/trades"
OUTPUT_PATH = ASSET_ROOT / "data/processed/kalshi/trades_native_count_fp.csv.gz"
AUDIT_ROOT = ASSET_ROOT / "data/audits"
PER_MARKET_AUDIT_PATH = AUDIT_ROOT / "kalshi_fractional_trade_audit.csv"
SUMMARY_PATH = AUDIT_ROOT / "kalshi_fractional_trade_summary.json"
MANIFEST_PATH = ASSET_ROOT / "data/processed/kalshi_native_trades_manifest.json"
SCHEMA_VERSION = "2026-09-07.1"

AUDIT_FIELDS = [
    "pair_id",
    "kalshi_ticker",
    "observation_period",
    "native_trades",
    "predexon_trades",
    "matched_trade_ids",
    "native_only_trade_ids",
    "predexon_only_trade_ids",
    "fractional_native_trades",
    "subcent_native_trades",
    "matched_quantity_discrepancies",
    "matched_price_discrepancies",
    "native_contracts",
    "predexon_contracts_matched",
    "native_contracts_matched",
    "signed_quantity_error",
    "absolute_quantity_error",
    "maximum_absolute_trade_error",
    "signed_price_error",
    "absolute_price_error",
    "maximum_absolute_price_error",
]


def as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def decimal_text(value: Decimal) -> str:
    """Serialize a Decimal without scientific notation."""

    return format(value, "f")


def native_trade_timestamp(trade: dict) -> tuple[int, int]:
    """Return native Kalshi trade time as epoch seconds and milliseconds."""

    raw = trade.get("created_time")
    if raw in (None, ""):
        raise RuntimeError("Native Kalshi trade is missing created_time")
    if isinstance(raw, (int, float)) or str(raw).replace(".", "", 1).isdigit():
        numeric = float(raw)
        if numeric >= 1_000_000_000_000:
            return int(numeric // 1_000), int(numeric)
        return int(numeric), int(numeric * 1_000)
    parsed = parse_utc(str(raw))
    return int(parsed.timestamp()), int(parsed.timestamp() * 1_000)


def normalize_native_trade(pair: dict[str, str], trade: dict) -> dict:
    """Normalize one official Kalshi trade while preserving fixed-point size."""

    trade_id = str(trade.get("trade_id") or "").strip()
    if not trade_id:
        raise RuntimeError("Native Kalshi trade is missing trade_id")
    returned_ticker = str(trade.get("ticker") or "")
    if returned_ticker and returned_ticker != pair["kalshi_ticker"]:
        raise RuntimeError(
            f"Kalshi returned ticker {returned_ticker} while requesting "
            f"{pair['kalshi_ticker']}"
        )

    size = as_decimal(trade.get("count_fp"))
    if size is None:
        raise RuntimeError(
            f"Native Kalshi trade {trade_id} is missing exact count_fp"
        )
    yes_price = as_decimal(
        trade.get("yes_price_dollars", trade.get("yes_price"))
    )
    if yes_price is None:
        raise RuntimeError(
            f"Native Kalshi trade {trade_id} is missing yes_price_dollars"
        )
    timestamp, timestamp_ms = native_trade_timestamp(trade)

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
        "trade_id": trade_id,
        "timestamp": timestamp,
        "timestamp_ms": timestamp_ms,
        "observation_period": treatment_period(timestamp),
        "treatment_received": False,
        "taker_delay_ms": 0,
        "incident_window": False,
        "price": decimal_text(yes_price),
        "canonical_yes_price": decimal_text(yes_price),
        "size": decimal_text(size),
        "size_source": "kalshi_native_count_fp",
        "size_is_exact": True,
        "notional_usd": decimal_text(yes_price * size),
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


def trade_segments(
    start: int,
    end: int,
    cutoff: int,
) -> list[tuple[str, str, int, int]]:
    """Route a requested interval around Kalshi's live/historical cutoff."""

    if not start < end:
        raise ValueError("Trade interval must have positive duration")
    segments: list[tuple[str, str, int, int]] = []
    if start < cutoff:
        segments.append(("historical", "historical/trades", start, min(end, cutoff)))
    if end > cutoff:
        segments.append(("live", "markets/trades", max(start, cutoff), end))
    return [segment for segment in segments if segment[2] < segment[3]]


def write_part(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_part(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def collect_market(
    client: KalshiPublicClient,
    pair: dict[str, str],
    cutoff: int,
    overwrite: bool,
) -> tuple[Path, list[dict[str, str]], Counter[str], bool]:
    """Fetch or reuse one market and return rows plus page counts."""

    part_path = NATIVE_PART_ROOT / f"{safe_filename(pair['pair_id'])}.csv.gz"
    if part_path.exists() and not overwrite:
        return part_path, read_part(part_path), Counter(), True

    start = epoch_seconds(pair["comparison_start_utc"])
    end = epoch_seconds(pair["comparison_end_utc"])
    by_trade_id: dict[str, dict] = {}
    pages: Counter[str] = Counter()

    for segment_name, endpoint, segment_start, segment_end in trade_segments(
        start, end, cutoff
    ):
        params = {
            "ticker": pair["kalshi_ticker"],
            # The one-second overlap protects boundary trades; exact filtering
            # and trade_id de-duplication below remove any overlap.
            "min_ts": max(0, segment_start - 1),
            "max_ts": segment_end,
            "limit": 1000,
        }
        raw_dir = RAW_ROOT / safe_filename(pair["pair_id"]) / segment_name
        for page_number, payload in client.cursor_pages(endpoint, params, "trades"):
            pages[segment_name] += 1
            write_json_gzip_atomic(
                payload,
                raw_dir / f"page_{page_number:05d}.json.gz",
            )
            for trade in payload["trades"]:
                row = normalize_native_trade(pair, trade)
                if start <= int(row["timestamp"]) < end:
                    existing = by_trade_id.get(str(row["trade_id"]))
                    if existing is not None and existing != row:
                        raise RuntimeError(
                            f"Conflicting duplicate native trade_id {row['trade_id']}"
                        )
                    by_trade_id[str(row["trade_id"])] = row

    rows = sorted(
        by_trade_id.values(),
        key=lambda row: (int(row["timestamp_ms"]), str(row["trade_id"])),
    )
    write_part(rows, part_path)
    return part_path, rows, pages, False


def predexon_values(pair_id: str) -> dict[str, tuple[Decimal, Decimal]]:
    """Read saved Predexon size and YES-price values for one market."""

    path = PREDEXON_PART_ROOT / f"{safe_filename(pair_id)}.csv.gz"
    if not path.exists():
        return {}
    values: dict[str, tuple[Decimal, Decimal]] = {}
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            trade_id = str(row.get("trade_id") or "").strip()
            size = as_decimal(row.get("size"))
            price = as_decimal(row.get("canonical_yes_price"))
            if trade_id and size is not None and price is not None:
                values[trade_id] = (size, price)
    return values


def reconcile_market(
    pair: dict[str, str],
    native_rows: list[dict[str, str]],
) -> dict[str, object]:
    """Compare native exact quantities with the prior Predexon market part."""

    native_values = {
        str(row["trade_id"]): (
            as_decimal(row.get("size")),
            as_decimal(row.get("canonical_yes_price")),
        )
        for row in native_rows
    }
    if any(
        size is None or price is None for size, price in native_values.values()
    ):
        raise RuntimeError(f"Missing native size or price in {pair['pair_id']}")
    exact_sizes: dict[str, Decimal] = {
        trade_id: size
        for trade_id, (size, _) in native_values.items()
        if size is not None
    }
    exact_prices: dict[str, Decimal] = {
        trade_id: price
        for trade_id, (_, price) in native_values.items()
        if price is not None
    }
    rounded_values = predexon_values(pair["pair_id"])
    rounded_sizes = {
        trade_id: values[0] for trade_id, values in rounded_values.items()
    }
    rounded_prices = {
        trade_id: values[1] for trade_id, values in rounded_values.items()
    }
    native_ids = set(exact_sizes)
    predexon_ids = set(rounded_values)
    matched_ids = native_ids & predexon_ids

    fractional = sum(
        1 for value in exact_sizes.values() if value != value.to_integral_value()
    )
    subcent = sum(
        1
        for value in exact_prices.values()
        if value * Decimal("100") != (value * Decimal("100")).to_integral_value()
    )
    quantity_differences = [
        rounded_sizes[trade_id] - exact_sizes[trade_id]
        for trade_id in matched_ids
        if rounded_sizes[trade_id] != exact_sizes[trade_id]
    ]
    price_differences = [
        rounded_prices[trade_id] - exact_prices[trade_id]
        for trade_id in matched_ids
        if rounded_prices[trade_id] != exact_prices[trade_id]
    ]
    absolute_quantity_error = sum(
        (abs(value) for value in quantity_differences), Decimal("0")
    )
    maximum_quantity_error = max(
        (abs(value) for value in quantity_differences), default=Decimal("0")
    )
    absolute_price_error = sum(
        (abs(value) for value in price_differences), Decimal("0")
    )
    maximum_price_error = max(
        (abs(value) for value in price_differences), default=Decimal("0")
    )
    native_matched = sum(
        (exact_sizes[trade_id] for trade_id in matched_ids), Decimal("0")
    )
    predexon_matched = sum(
        (rounded_sizes[trade_id] for trade_id in matched_ids), Decimal("0")
    )

    return {
        "pair_id": pair["pair_id"],
        "kalshi_ticker": pair["kalshi_ticker"],
        "observation_period": pair["market_period"],
        "native_trades": len(native_ids),
        "predexon_trades": len(predexon_ids),
        "matched_trade_ids": len(matched_ids),
        "native_only_trade_ids": len(native_ids - predexon_ids),
        "predexon_only_trade_ids": len(predexon_ids - native_ids),
        "fractional_native_trades": fractional,
        "subcent_native_trades": subcent,
        "matched_quantity_discrepancies": len(quantity_differences),
        "matched_price_discrepancies": len(price_differences),
        "native_contracts": decimal_text(sum(exact_sizes.values(), Decimal("0"))),
        "predexon_contracts_matched": decimal_text(predexon_matched),
        "native_contracts_matched": decimal_text(native_matched),
        "signed_quantity_error": decimal_text(predexon_matched - native_matched),
        "absolute_quantity_error": decimal_text(absolute_quantity_error),
        "maximum_absolute_trade_error": decimal_text(maximum_quantity_error),
        "signed_price_error": decimal_text(sum(price_differences, Decimal("0"))),
        "absolute_price_error": decimal_text(absolute_price_error),
        "maximum_absolute_price_error": decimal_text(maximum_price_error),
    }


def combine_parts(part_paths: list[Path], output: Path) -> int:
    """Stream native parts into one gzip CSV without loading all trades."""

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    count = 0
    with gzip.open(temporary, "wt", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS)
        writer.writeheader()
        for part in sorted(part_paths):
            with gzip.open(part, "rt", newline="", encoding="utf-8") as source:
                for row in csv.DictReader(source):
                    writer.writerow(row)
                    count += 1
    temporary.replace(output)
    return count


def build_summary(audits: list[dict[str, object]]) -> dict[str, object]:
    """Build the concise totals needed to answer Brian's first question."""

    def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
        native_trades = sum(int(row["native_trades"]) for row in rows)
        fractional_trades = sum(int(row["fractional_native_trades"]) for row in rows)
        subcent_trades = sum(int(row["subcent_native_trades"]) for row in rows)
        return {
            "markets": len(rows),
            "markets_with_native_trades": sum(
                int(row["native_trades"]) > 0 for row in rows
            ),
            "markets_with_fractional_trades": sum(
                int(row["fractional_native_trades"]) > 0 for row in rows
            ),
            "markets_with_subcent_trades": sum(
                int(row["subcent_native_trades"]) > 0 for row in rows
            ),
            "native_trades": native_trades,
            "fractional_native_trades": fractional_trades,
            "fractional_trade_share": (
                fractional_trades / native_trades if native_trades else None
            ),
            "subcent_native_trades": subcent_trades,
            "subcent_trade_share": (
                subcent_trades / native_trades if native_trades else None
            ),
            "matched_trade_ids": sum(int(row["matched_trade_ids"]) for row in rows),
            "native_only_trade_ids": sum(
                int(row["native_only_trade_ids"]) for row in rows
            ),
            "predexon_only_trade_ids": sum(
                int(row["predexon_only_trade_ids"]) for row in rows
            ),
            "matched_quantity_discrepancies": sum(
                int(row["matched_quantity_discrepancies"]) for row in rows
            ),
            "matched_price_discrepancies": sum(
                int(row["matched_price_discrepancies"]) for row in rows
            ),
            "native_contracts": decimal_text(
                sum(
                    (as_decimal(row["native_contracts"]) or Decimal("0") for row in rows),
                    Decimal("0"),
                )
            ),
            "signed_quantity_error": decimal_text(
                sum(
                    (
                        as_decimal(row["signed_quantity_error"]) or Decimal("0")
                        for row in rows
                    ),
                    Decimal("0"),
                )
            ),
            "absolute_quantity_error": decimal_text(
                sum(
                    (
                        as_decimal(row["absolute_quantity_error"]) or Decimal("0")
                        for row in rows
                    ),
                    Decimal("0"),
                )
            ),
            "signed_price_error": decimal_text(
                sum(
                    (as_decimal(row["signed_price_error"]) or Decimal("0") for row in rows),
                    Decimal("0"),
                )
            ),
            "absolute_price_error": decimal_text(
                sum(
                    (
                        as_decimal(row["absolute_price_error"]) or Decimal("0")
                        for row in rows
                    ),
                    Decimal("0"),
                )
            ),
        }

    return {
        "definition": (
            "A market is affected when at least one native Kalshi trade has a "
            "non-integer count_fp quantity. Quantity discrepancies are measured "
            "only for trade_ids present in both sources. Sub-cent trades are "
            "reported separately because native yes_price_dollars can also be "
            "more precise than Predexon's trade price."
        ),
        "overall": summarize(audits),
        "by_period": {
            period: summarize(
                [row for row in audits if row["observation_period"] == period]
            )
            for period in ("pre", "post")
        },
    }


def main(run_log: RunLog) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--overwrite-parts", action="store_true")
    args = parser.parse_args()

    pairs = read_csv(MAP_PATH)
    if args.max_markets > 0:
        pairs = pairs[: args.max_markets]

    client = KalshiPublicClient(
        request_interval=args.request_interval,
        run_log=run_log,
    )
    cutoff_payload = client.get_json("historical/cutoff")
    if cutoff_payload is None or not cutoff_payload.get("trades_created_ts"):
        raise RuntimeError("Kalshi historical cutoff response is incomplete")
    cutoff = epoch_seconds(str(cutoff_payload["trades_created_ts"]))
    run_log.info(
        f"Native Kalshi trade collection configured pairs={len(pairs)} "
        f"partial_run={args.max_markets > 0} "
        f"overwrite_parts={args.overwrite_parts} "
        f"trades_cutoff_utc={cutoff_payload['trades_created_ts']} "
        f"market_map={MAP_PATH}"
    )

    part_paths: list[Path] = []
    audits: list[dict[str, object]] = []
    pages: Counter[str] = Counter()
    reused_parts = 0
    empty_markets = 0

    for index, pair in enumerate(pairs, start=1):
        part_path, rows, market_pages, reused = collect_market(
            client,
            pair,
            cutoff,
            args.overwrite_parts,
        )
        part_paths.append(part_path)
        audits.append(reconcile_market(pair, rows))
        pages.update(market_pages)
        reused_parts += int(reused)
        empty_markets += int(not rows)
        run_log.progress(
            index,
            len(pairs),
            f"pair={pair['pair_id']} rows={len(rows)} "
            f"pages={dict(market_pages)} source={'reused' if reused else 'fetched'} "
            f"fractional_trades={audits[-1]['fractional_native_trades']} "
            f"subcent_trades={audits[-1]['subcent_native_trades']}",
        )

    output_rows = combine_parts(part_paths, OUTPUT_PATH)
    write_csv_atomic(audits, PER_MARKET_AUDIT_PATH, AUDIT_FIELDS)
    summary = build_summary(audits)
    summary.update(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "official_kalshi_public_api",
            "log_file": str(run_log.path),
        }
    )
    write_json_atomic(summary, SUMMARY_PATH)
    write_json_atomic(
        {
            "schema_version": SCHEMA_VERSION,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "official_kalshi_public_api",
            "base_url": BASE_URL,
            "authentication_required": False,
            "endpoints": [
                "/historical/cutoff",
                "/markets/trades",
                "/historical/trades",
            ],
            "cutoff": cutoff_payload,
            "market_map": str(MAP_PATH),
            "pairs_selected": len(pairs),
            "partial_run": args.max_markets > 0,
            "api_pages": dict(pages),
            "reused_parts": reused_parts,
            "empty_markets": empty_markets,
            "output_rows": output_rows,
            "output_file": str(OUTPUT_PATH),
            "predexon_rounded_file_preserved": str(
                ASSET_ROOT / "data/processed/kalshi/trades.csv.gz"
            ),
            "per_market_audit": str(PER_MARKET_AUDIT_PATH),
            "fractional_summary": str(SUMMARY_PATH),
            "log_file": str(run_log.path),
        },
        MANIFEST_PATH,
    )
    run_log.info(
        f"Native Kalshi trade output rows={output_rows} pages={dict(pages)} "
        f"reused_parts={reused_parts} empty_markets={empty_markets} "
        f"affected_markets={summary['overall']['markets_with_fractional_trades']} "
        f"fractional_trades={summary['overall']['fractional_native_trades']} "
        f"requests={client.request_count} retries={client.retry_count} "
        f"output={OUTPUT_PATH} audit={PER_MARKET_AUDIT_PATH} "
        f"summary={SUMMARY_PATH} manifest={MANIFEST_PATH}"
    )


if __name__ == "__main__":
    run_logged("fetch_kalshi_native_trades", main)
