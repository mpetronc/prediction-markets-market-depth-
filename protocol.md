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
