## Project log

### 11.07.2026:
- Create Github project
- Find feasible dataset
- Organize workflow 

### 12.07.2026:
- Compared NFl vs NCAA(March Madness) in data
- Chose March Madness as first market  

### 13.07.2026-16.07.2026:
- Evaluated possible data-source combinations
-Selected a preliminary datasource setup and identified which required components are still missing.
> **Chosen datasource setup**
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