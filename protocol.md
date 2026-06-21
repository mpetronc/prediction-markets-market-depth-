## Project log

### 11.07.2026:
- Create GitHub project
- Find feasible dataset
- Organize workflow 

### 12.07.2026:
- Compared NFL vs NCAA(March Madness) in data
- Chose March Madness as first market  

### 13.07.2026-16.07.2026:
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
### 18.07.2026
- Built the testing pipeline to find March Madness markets
- Tested the Kaggle Polymarket files locally
- Confirmed that Kaggle contains NCAA CBB markets (seriesSlug = ncaa-cbb), but only for regular-season games from Nov/Dec 2025, not March Madness 
- Identified GitHub Polymarket dataset as a more promising source because it includes larger market metadata and trade-level data
- Next step: test the GitHub Polymarket dataset locally (looks promising).

### 19.07.2026
- Cleaned and simplified project structure
- Decided to use Predexon as main source for BBO snapshots and trade data
- Clarified that Kaggle/GitHub are only backup/reference sources
- Set up folders for raw metadata, matched markets, processed BBO/trades, and results

### 20.07.2026
- Gathered all relevant March Madness game-winner markets for both Kalshi and Polymarket
- Completed the metadata recovery step for missing Polymarket markets
- Used Polymarket event links/slugs to recover archived March Madness market metadata
- Added the recovered Polymarket winner markets to the project dataset
- Filtered out non-winner markets such as spreads and over/under markets
- Combined the original and recovered Polymarket market files into one master file
- Confirmed that the final Polymarket dataset contains 67 unique March Madness game markets
- Confirmed that the Kalshi and Polymarket metadata files now both contain 67 unique games
- Prepared both platform datasets for the next matching step
- Next step is to create the final matched Kalshi-Polymarket market file