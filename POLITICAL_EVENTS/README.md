# Political event markets

This folder contains the political event families used as comparison markets around the Polymarket taker-delay change on 17 August 2026 at 11:00 UTC.

## Event families

- `BRAZIL_PRESIDENTIAL_ELECTION`: matched candidate-winner contracts.
- `ICELAND_EU_REFERENDUM`: the matched Yes/No referendum contract.
- `MASSACHUSETTS_DEMOCRATIC_PRIMARIES`: matched Senate and House primary contracts.

## Standard layout

Each event family follows the same structure:

- `data/raw`: original Kalshi, native Kalshi, and Polymarket responses.
- `data/matched_markets`: cross-platform contract mappings.
- `data/processed`: normalized trades, BBO observations, and intermediate parts.
- `data/audits`: coverage and validation outputs.
- `data/logs`: collection logs.
- `data/results`: analysis outputs.
- `src/data_collection`: market discovery, mapping, and trade/BBO collection.
- `src/processing`: normalization, window filtering, and data-quality checks.
- `src/computation`: summary metrics, regression panels, and treatment-effect analysis.

The data directories are excluded by the repository `.gitignore` and are created locally by the pipeline as needed. Source code and documentation remain trackable.

## Implemented pipeline

The three event folders use one tested shared engine, `pipeline.py`, with an
`event_config.json` file and a thin `run_pipeline.py` entry point in each event
folder. The stages are:

1. `build-map`: validates each configured contract against official Kalshi and
   Polymarket metadata and enforces full-window coverage.
2. `fetch-trades`: collects exact fixed-point Kalshi trades and Polymarket
   transactions. Polymarket matched-order legs are collapsed to one aggregate
   taker leg per transaction hash to prevent double counting.
3. `fetch-bbo`: downloads resumable historical order-book snapshots, normalizes
   prices, and calculates best-level depth, full-book depth, and true notional
   depth separately.
4. `audit`: reports pre/post trade counts, notional, hourly trade coverage, and
   usable two-sided BBO coverage for every pair and platform.
5. `analyze`: performs the fully offline part of the workflow. It builds a
   balanced 336-hour panel for each pair and platform, carries quotes forward
   without backfilling future information, records quote staleness, computes
   trade and liquidity metrics, simulates price impact for 100/500/1,000
   contracts, and writes paired platform and pre/post comparisons.

All normalized prices express the configured candidate or referendum outcome's
YES probability. Political contracts are control markets, so
`treatment_received` is always false; the delay intervention applies to the
separate Polymarket crypto sample.

## Running an event

Install `requirements.txt`, copy the repository `.env.example` to `.env`, add
your `PREDX_API_KEY`, and run an event from the repository root. For example:

```bash
python3 -m pip install -r POLITICAL_EVENTS/requirements.txt
```

```bash
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland run-all
```

The four stages can also be run separately for easier inspection or recovery:

```bash
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland build-map
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland fetch-trades
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland fetch-bbo
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland audit
python3 POLITICAL_EVENTS/run_pipeline.py --event iceland analyze
```

Replace `iceland` with `brazil` or `massachusetts`. Use `--tier primary` on a
fetch stage for the preferred panel, or `--overwrite-parts` to deliberately
replace resumable per-market outputs. For a one-market pilot, pass the same
`--max-markets 1` option to the fetch and `analyze` stages; `run-all` propagates
it automatically. The official metadata and Kalshi trade stages do not use
Predexon; Polymarket trades and both BBO histories do.

## Outputs

- Exact mappings: `data/matched_markets/market_map.csv`
- Normalized trades: `data/processed/{platform}/trades.csv.gz`
- Normalized books: `data/processed/{platform}/bbo.csv.gz`
- Coverage audit: `data/audits/coverage_by_pair_platform_period.csv`
- Regression-ready hourly panel: `data/results/hourly_market_panel.csv.gz`
- Pair/platform/period metrics: `data/results/metrics_by_pair_platform_period.csv`
- Polymarket-minus-Kalshi comparisons: `data/results/platform_differences_by_pair_period.csv`
- Pre/post changes: `data/results/pre_post_changes_by_pair_platform.csv`
- Run manifests and timestamped logs alongside each stage's outputs

After all three event analyses finish, combine them with:

```bash
python3 POLITICAL_EVENTS/run_combined_analysis.py
```

This writes the complete political-control panel under
`POLITICAL_EVENTS/data/results/`. No additional API calls are made by either
the `analyze` stage or the combined analysis script.

Raw responses and generated datasets stay ignored by Git. Code, configurations,
and documentation remain visible on GitHub.
