## Project log

### 11.06.2026:
- Create GitHub project
- Find feasible dataset
- Organize workflow 

### 12.06.2026:
- Compared NFL vs NCAA(March Madness) in data
- Chose March Madness as first market  

### 13.06.2026-16.06.2026:
- Evaluated possible data-source combinations
- Selected a preliminary data-source setup and identified which required components are still missing.
> **Preliminary datasource setup**
>
| Data Needed | Source | Gathered? |
|---|---|---|
| Polymarket events | Kaggle `polymarket_events.csv` | True |
| Polymarket market summaries | Kaggle `polymarket_markets.csv` | True |
| Polymarket BBO timeline | TBD / still missing | False |
| Polymarket trades timeline | TBD / still missing | False |
| Polymarket metadata validation | Official Polymarket API | Planned |
| Kalshi BBO timeline | Predexon | Planned |
| Kalshi trades timeline | Official Kalshi API | Planned |
| Kalshi metadata | Official Kalshi API | Planned |
> This setup uses each source for what it is strongest at: Kaggle for Polymarket, Predexon for Kalshi historical BBO, and official APIs for metadata and trade validation. Whilst taking performance into account.

### Kaggle Polymarket Dataset Structure
- Looked at Kaggle data and created a mental mindmap:
```text

├── Events File
│   ├── Describes high-level events
│   ├── Example: "Duke vs UNC"
│   ├── Useful columns:
│   │   ├── id
│   │   ├── title
│   │   ├── slug
│   │   ├── homeTeamName / awayTeamName
│   │   ├── gameId
│   │   ├── startTime / startDate
│   │   ├── volume
│   │   └── liquidity
│   └──  Use:
│       ├── Find March Madness games
│       ├── Identify event-level metadata
│       └── Link events to prediction markets
│
├── Markets File
│   ├── Describes individual prediction markets under each event
│   ├── Example: "Will Duke win?"
│   ├── Useful columns:
│   │   ├── id
│   │   ├── conditionId
│   │   ├── question
│   │   ├── event_id
│   │   ├── event_title
│   │   ├── clobTokenIds
│   │   ├── outcomes
│   │   ├── bestBid / bestAsk
│   │   ├── volumeClob
│   │   ├── liquidityClob
│   │   └── gameStartTime
│   └──  Use:
│       ├── Find the exact Polymarket market
│       ├── Extract conditionId
│       ├── Extract YES/NO token IDs
│       ├── Check market volume and liquidity
│       └── Match the market to the corresponding Kalshi ticker
```
### 18.06.2026
- Built the testing pipeline to find March Madness markets
- Tested the Kaggle Polymarket files locally
- Confirmed that Kaggle contains NCAA CBB markets (seriesSlug = ncaa-cbb), but only for regular-season games from Nov/Dec 2025, not March Madness 
- Identified GitHub Polymarket dataset as a more promising source because it includes larger market metadata and trade-level data
- Next step: test the GitHub Polymarket dataset locally (looks promising).

### 19.06.2026
- Cleaned and simplified project structure
- Decided to use Predexon as main source for BBO snapshots and trade data
- Clarified that Kaggle/GitHub are only backup/reference sources
- Set up folders for raw metadata, matched markets, processed BBO/trades, and results

### 20.06.2026
- Gathered all relevant March Madness game-winner markets for both Kalshi and Polymarket
- Completed the metadata recovery step for missing Polymarket markets
- Used Polymarket event links/slugs to recover archived March Madness market metadata
- Added the recovered Polymarket winner markets to the project dataset
- Filtered out non-winner markets such as spreads and over/under markets
- Combined the original and recovered Polymarket market files into one master file

### 21.06.2026
* Finalized the matched-market metadata pipeline
* Updated scripts to match the new project folder structure
* Matched Kalshi and Polymarket markets using cleaned unordered team pairs
* Confirmed that all 67 March Madness games were matched successfully
* Created MM_matched_markets.csv as the full matched metadata file
* Created MM_market_map.csv as the compact operational mapping file
* Mapped each game to a unique internal game_id
  * Included Kalshi event tickers and team-specific Kalshi market tickers
  * Included Polymarket event slugs, market IDs, condition IDs, and CLOB token IDs
  * Verified that all 67 games have complete Kalshi and Polymarket identifiers
* Confirmed there are no unmatched Kalshi or Polymarket markets
- Confirmed that the Kalshi and Polymarket metadata files now both contain 67 unique games
- Prepared both platform datasets for the next matching step
- Next step is to create the final matched Kalshi-Polymarket market file

### 28.06.2026-01.07.2026
- Completed full historical trade data collection for all 67 NCAA March Madness game-winner markets across Kalshi and Polymarket
- Collected over 4.1 million Kalshi trades and 759,000 Polymarket trades from game start through market resolution
- Successfully implemented historical best bid/offer (BBO) snapshot collection for both exchanges
- Collected approximately 2.66 million Kalshi BBO snapshots and 724,000 Polymarket BBO snapshots
- Identified and verified a single missing Kalshi BBO instrument (`KXNCAAMBGAME-26APR04MICHARIZ-ARIZ`), likely due to unavailable historical data from the Predexon API rather than a data collection error
- Validated dataset integrity by confirming all 67 games were processed, duplicate trade IDs were removed where appropriate, and all fetched data was written successfully to the master CSV datasets

### 07.07.2026-08.07.2026
* Clarified that Kyle lambda must be computed separately using time-bucketed delta_mid_price and signed_volume
* Decided not to merge Kyle lambda into the current summary-metrics file
* Optimized the summary-metrics script by adding BBO downsampling, faster one-pass price-impact computation, instrument-level summaries, fill rates, notional depth, and logging
* Added concurrent logging to both terminal output and data/results/compute_summary_metrics.log
* Started running the updated compute_summary_metrics.py script on the full BBO dataset with 3,284,162 cleaned BBO rows
* Fixed price normalization logic so already-decimal Kalshi and Polymarket prices are no longer incorrectly divided by 100
- Cleaned combined BBO data from 3,383,932 raw rows to 3,284,162 valid rows after removing invalid/missing quotes

- Confirmed full BBO coverage across 67 games and both platforms, with the known exception of missing Kalshi BBO data for `MM_GAME_007`

- Confirmed full trade coverage across 67 games and both platforms, with 4,929,106 valid trade rows

- Replaced full raw-book walking with sampled price-impact computation to avoid unnecessary runtime blowup

- Tested price-impact sampling at 10, 100, and 500 snapshots per instrument

- Increased final sampled price-impact setting to 500 snapshots per instrument, producing 131,967 sampled BBO rows

- Confirmed that full spread, depth, mid-price, volatility, volume, notional, and trade-count metrics still use the complete BBO/trade datasets

- Clarified that the current price-impact output is a simulated visible-order-book impact measure, not the real Kyle lambda price-impact estimate

- Saved updated outputs to `data/results/`, including sampled price-impact snapshots, instrument-level summary metrics, game/platform summary metrics, and the computation log

- Decided to keep 500 snapshots per instrument as a strong descriptive setting and move next to the Kyle lambda regression step

### 18.07.2026-19.07.2026

* Developed an ESPN data-collection script to retrieve the exact game-end timestamp for all 67 March Madness games
* Confirmed that all 67 games contained an explicit ESPN “End of Game” timestamp, with no missing values or fallbacks
* Added UTC, Unix-second, and Unix-millisecond cutoff timestamps to `data/game_cutoffs.csv`
* Developed the Polymarket timestamp-filtering pipeline and successfully matched all 67 market game IDs to their corresponding ESPN games
* Configured the current filter to use only the upper bound, retaining pregame and in-game observations while removing data recorded after the game ended
* Retained 707,662 of 724,096 Polymarket BBO snapshots and removed 16,434 postgame snapshots (2.27%)
* Retained 740,419 of 759,586 Polymarket trades and removed 19,167 postgame trades (2.52%)
* Confirmed that neither dataset contained invalid timestamps
* Saved the filtered datasets as `bbo_filtered.csv` and `trades_filtered.csv` under `data/processed/polymarket/filtered_bbo_trades`

### 20.07.2026

* Decided to define the analysis window inclusively as `ESPN first play <= observation timestamp <= ESPN game end`, excluding all pregame and postgame observations.
* The timestamp filter removed 52.87% of all collected rows: 83.20% of Kalshi BBOs, 35.04% of Kalshi trades, 51.26% of Polymarket BBOs, and 46.02% of Polymarket trades.
* Confirmed complete valid trade coverage for all 268 instruments and 67 games.
* Found usable BBO data for 256 instruments, allowing complete paired BBO analysis for 58 of 67 games.

#### Problems found

* Six Kalshi instruments had no in-game BBO snapshots despite active in-game trades.
* Six additional Kalshi instruments had BBO snapshots, but every snapshot was one-sided and unusable.
* Found 18,387 crossed Kalshi BBO snapshots and 498 crossed Polymarket snapshots in the raw provider data.
* Found 27,724 Kalshi snapshots with an empty side and 3,866 Polymarket snapshots with a missing side.
* The fetcher marked games complete even when an instrument returned zero BBO rows.
* The mappings and timestamps were verified, indicating a likely Predexon Kalshi BBO-history problem.
* Pending decision: determine whether the 58-game complete BBO sample is sufficient or whether another BBO source is required.

### 24.07.2026-26-07.2026:
- Corrected the upstream liquidity-metric computation without recollecting data or calling the Predexon API.
- Fixed Kalshi order-book price normalization, including one-cent price levels.
- Separated best-level depth from full-book depth and calculated true notional depth as the sum of price × size across order-book levels.
- Corrected Kalshi trade orientation so YES- and NO-side trades are consistently expressed as the selected team outcome’s YES probability.
- Selected one team outcome per game to avoid double-counting complementary contracts.
- Constructed a 67-game trade sample and a 64-game joint BBO sample.
- Used Team B by default, with two Team A overrides where only that side had usable Kalshi BBO data:
  - MM_GAME_037: Tennessee State
  - MM_GAME_056: Vanderbilt
- Retained three games for trade analysis but excluded them from BBO analysis:
  - MM_GAME_006: Arizona
  - MM_GAME_007: Michigan
  - MM_GAME_028: Houston
- Generated 37 paired platform metrics with 2,384 game-metric observations.
- Descriptive results indicate that spreads are similar, Kalshi has substantially greater displayed depth, Kalshi generally has lower simulated price impact, and Kalshi has approximately four times the trading activity and notional. Trade-price volatility is similar across platforms.
- Validated the regenerated CSV and Parquet outputs against the raw order books and trades.
- Next step: estimate paired game-level regressions with game fixed effects and conduct robustness tests.

### 24.08.2026-01.09.2026:
- Reorganized the cryptocurrency project into `CRYPTO_UP_DOWN` for 15-minute relative-price markets and `CRYPTO_HOURLY` for hourly fixed-strike markets, with matching BTC, ETHEREUM, and SOLANA directories.
- Removed 42 redundant empty files inherited from  March Madness structure.
- Designed a two-stage pipeline that first discovers and exactly matches eligible Polymarket and Kalshi contracts and then fetches trades and order books only for the approved market registry (similar to MM).
- Limited the initial BTC collection to two weeks in total: 10-17 August for the pre-period and 17-24 August for the post-period. A separate Polymarket crypto-resolution delay on 17 August will be flagged for later robustness testing.
- Next step: validate Predexon using one BTC market from each venue and contract family, then implement the complete BTC discovery and data-fetching pipelines before extending them to ETH and SOL.

### 02.09.2026-07.09.2026:
- Completed BTC 15-minute trade collection and BTC 15-minute/hourly BBO collection for the matched two-week sample.
- Confirmed that Predexon rounds Kalshi's fractional trade quantity to integer `count` and loses sub-cent trade-price precision, while Kalshi's public API exposes exact `count_fp` and `yes_price_dollars` values.
- Preserved the completed Predexon and Polymarket files and added a separate, resumable native-Kalshi trade collector that uses no API key.
- Added trade-ID reconciliation and pre/post summaries to measure affected markets, affected trades, and quantity error before replacing any analytical input.
- Added a native-Kalshi audit for the 142 expected BTC 15-minute intervals absent from the Predexon discovery results.
- Next step: run both native-Kalshi stages, review their audit summaries, and then promote the exact trade file for downstream processing.

### 17.08.2026:

- Began considering Polymarket’s reduction of the crypto-market taker delay from 250 ms to 50 ms as a potential natural experiment for comparing market quality before and after the change.

### 02.09.2026-13.09.2026:

- Built and tested complete discovery, exact-matching, trade, BBO, and native-Kalshi collection pipelines for the BTC, ETH, and SOL cryptocurrency samples.
- Completed the 15-minute Up/Down collection with 1,202 BTC, 1,242 ETH, and 1,298 SOL matched market pairs.
- Completed the hourly fixed-strike collection with 5,116 BTC and 4,120 ETH matched pairs, covering the one-week pre- and post-treatment periods.
- Identified that Predexon rounds fractional Kalshi quantities and loses some price precision; retrieved exact `count_fp` and dollar-price fields through Kalshi’s public API and reconciled them using trade IDs.
- Audited missing 15-minute intervals and distinguished Predexon omissions from 18 markets that were not offered during Kalshi’s scheduled maintenance windows.
- Added resumable collection, checkpointing, automatic retries, progress logs, and validation summaries across the crypto pipelines.
- Validated and packaged the BTC and ETH trades and BBO snapshots as compressed CSV files, with separate fractional-trade audit summaries.
- Confirmed that Polymarket did not offer an hourly SOL fixed-strike series. Consequently, SOL remains in the 15-minute analysis, while the hourly analysis is restricted to BTC and ETH.

### 14.09.2026-22.09.2026:

- Evaluated cross-listed political markets using full two-week coverage, contract comparability, liquidity, rule consistency and resolution timing around the 17 August 2026 intervention.
- Selected three political control families: the Brazil presidential election, Iceland EU-negotiations referendum and Massachusetts Democratic primaries.
- Verified 25 matched Kalshi–Polymarket contracts against official metadata:
  - Brazil: 12 candidates, including three primary contracts.
  - Iceland: one referendum contract.
  - Massachusetts: 12 candidates across five primaries, including seven primary contracts.
- Created a standardized directory and configuration structure for all three event families.
- Built a reusable pipeline for market mapping, trade collection, historical BBO collection, normalization, auditing and offline analysis.
- Used exact Kalshi fixed-point trade quantities and corrected Polymarket double-counting by collapsing matched order legs into one transaction per transaction hash.
- Added resumable per-market downloads, retries, raw-response preservation, manifests and timestamped logs.
- Marked the political markets as untreated controls because the Polymarket delay change applied to crypto markets.
- Built balanced 336-hour panels with spreads, best-level and full-book depth, notional depth, quote staleness, trade activity and simulated price impact for 100, 500 and 1,000 contracts.
- Added paired platform comparisons, pre/post changes, quality diagnostics and a script combining all three political event families.
- Added ten automated tests covering configurations, trade orientation, transaction aggregation, BBO normalization, depth calculations and panel construction.
- Completed an end-to-end Iceland validation: 104 Kalshi trades, 185 Polymarket transactions, 26,752 Kalshi BBO observations, 146 Polymarket BBO observations and 672 hourly panel rows.
- Reproduced the expected Lula trade counts and successfully validated the Ed Markey contract as additional live tests.
- Added Git rules that retain scripts, configurations and directory structure while excluding downloaded and generated datasets.
- Next step: run the complete Brazil and Massachusetts collections, combine all three political panels and inspect coverage before integrating them into the main empirical analysis.