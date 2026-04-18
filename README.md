# NBA Prop AI — True Probability Parlay Builder

NBA Prop AI computes **true probability** for NBA player props by building a multi-factor statistical model from raw data, then comparing that model probability to the sportsbook's implied probability to identify edges.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure API keys
cp .env.template .env
# Edit .env and fill in: SPORTSDATAIO_API_KEY, SPORTRADAR_API_KEY, THE_ODDS_API_KEY

# 3. Run
python main.py

# With options
python main.py --min-edge 0.07 --max-legs 2 --stake 200
python main.py --date 2025-04-14 --prop-types points assists
python main.py --debug-api   # print raw API responses
```

---

## Data Architecture

The engine uses **four data sources** with strict role separation.

### Source of Truth by Model Factor

| Factor | Primary Source | Notes |
|---|---|---|
| Usage rate | **nba_api** | `LeagueDashPlayerStats` Advanced, `USG_PCT` |
| Touches | **nba_api** | `LeagueDashPtStats` Possessions |
| Possessions / game | **nba_api** | `LeagueDashPlayerStats` Advanced, `POSS` |
| Pace context | **nba_api** | `LeagueDashTeamStats` Advanced, `PACE` |
| Splits (home/away/last-N) | **nba_api** | `LeagueDashPlayerStats` with split filters |
| Recent game logs | **SportsDataIO** primary, nba_api supplement | Last 5 / last 10 game rolling averages |
| Season averages (box score) | **SportsDataIO** | `PlayerSeasonStats` endpoint |
| Injuries / availability | **SportsDataIO** | `InjuredPlayers` endpoint (15-min refresh) |
| Depth charts / lineups / role | **SportsDataIO** | `PlayersByActive` + `ProjectedPlayerGameStats` |
| Minutes / projected role | **SportsDataIO** | Projected lineup endpoint |
| Team defensive stats | **SportsDataIO** | `TeamSeasonStats` opponent columns |
| DvP (Defense vs Position) | **Derived internally** | Built from nba_api + SportsDataIO logs |
| Matchup context | **Blended** | SportsDataIO defense + nba_api pace |
| Sportsbook odds / lines | **The Odds API** | Sole source for all pricing |
| Implied probability | **The Odds API** | Shin vig-removal applied |
| Game slate / schedule | **Sportradar** primary | SportsDataIO as fallback |

### What Each Source Is Used For

**nba_api** (no API key required)
- Usage rate, touches, time of possession
- Possessions per game, team pace
- Home/away/last-N game splits
- Advanced player tracking context
- Recent game logs (per player, cached)

**SportsDataIO** (API key required)
- Player injury status and availability
- Projected starters, depth charts, lineup context
- Season-average box-score stats
- Player game logs (recent form)
- Team defensive stats (for matchup context)
- Game schedule (slate fallback)

**The Odds API** (API key required)
- All sportsbook prop lines and prices
- Over/under odds for implied probability
- Line shopping across books
- NOT used for any player stat modeling

**Sportradar** (API key required)
- Game schedule / slate (primary)
- Game IDs and matchup context
- NOT used for player stat modeling or odds

---

## Pipeline

The daily evaluation pipeline runs in this order:

```
STEP 1  Pull today's slate
        SOURCE: Sportradar → SportsDataIO (fallback)
        Output: game IDs, team matchups

STEP 2  Pull prop markets and odds
        SOURCE: The Odds API
        Output: OddsLine list (player_id, line, over/under odds, book)

STEP 3  Warm service caches (batch API pulls — once per day)
        nba_api   → UsageTrackingService, SplitsService
        SportsDataIO → PlayerContextService, InjuryContextService, MatchupContextService
        NOTE: nba_api is called in league-wide batches here, NEVER per-prop

STEP 4  Build Player objects
        SOURCE: PlayerContextService (SportsDataIO season stats + nba_api advanced)

STEP 5  Build feature store
        SOURCE: PlayerFeatureBuilder → blends all service outputs
        Output: FeatureVector per (player, prop_type)

STEP 6  Evaluate each prop
        - Retrieve FeatureVector from store (no live API calls here)
        - Hydrate Player entity with nba_api advanced fields
        - Run stat model → StatProjection
        - Compute true probability from distribution
        - Compute implied probability from Odds API (vig-removed)
        - Calculate edge = true_prob − implied_prob
```

---

## DvP Construction

Defense vs Position (DvP) is computed internally — no external DvP provider is used.

**Formula:**

```
key = (defense_team_id, player_position)   # PG / SG / SF / PF / C

fantasy_points = pts + 1.2×reb + 1.5×ast + 3×stl + 3×blk − tov

normalized_dvp = team_allowed_stat / league_avg_for_position_stat
  > 1.0 → weaker defense vs that position/stat (favorable for bettor)
  < 1.0 → stronger defense
```

**Windows computed:** season, last 10 games, last 5 games.

**Position sourcing:** SportsDataIO depth chart first, nba_api metadata fallback,
internal bucket map (G→PG/SG, F→SF/PF, C→C).

---

## nba_api Request Strategy

nba_api is rate-conscious by design:

- All pulls use **league-wide dashboards** (one request returns all 400+ players)
- Results are **cached to disk** under `data/cache/nba_api/` with 24-hour TTL
- A **0.6-second sleep** is inserted between sequential API calls
- nba_api is **never called inside the per-prop evaluation loop**
- Retry logic handles transient NBA.com failures gracefully

| Endpoint | Batch granularity | Cache TTL |
|---|---|---|
| `LeagueDashPlayerStats` (Advanced) | Full league | 24h |
| `LeagueDashPtStats` (Possessions) | Full league | 24h |
| `LeagueDashPtStats` (Passing) | Full league | 24h |
| `LeagueDashPtStats` (Rebounding) | Full league | 24h |
| `LeagueDashTeamStats` (Advanced) | Full league | 24h |
| `LeagueDashPlayerStats` (splits) | Full league | 24h |
| `PlayerGameLog` | Per player | 12h |

---

## Project Structure

```
True_Prob_AI/
├── config.py                      # env var loading, provider credentials
├── main.py                        # CLI entry point
│
├── providers/
│   ├── nba_api_provider.py        # nba_api (PRIMARY: usage/tracking/splits)
│   ├── sportsdataio_provider.py   # SportsDataIO (PRIMARY: injuries/stats/rosters)
│   ├── odds_api_provider.py       # The Odds API (ONLY: sportsbook pricing)
│   ├── sportradar_provider.py     # Sportradar (slate only)
│   └── provider_registry.py      # routes requests to correct provider group
│
├── services/
│   ├── cache_service.py           # disk + in-memory TTL cache
│   ├── player_context_service.py  # orchestrates player data from all sources
│   ├── injury_context_service.py  # SportsDataIO injuries + vacancy factor
│   ├── usage_tracking_service.py  # nba_api advanced + tracking dashboards
│   ├── splits_service.py          # nba_api home/away/last-N splits
│   ├── matchup_context_service.py # team defense + pace (SportsDataIO + nba_api)
│   └── dvp_service.py             # DvP table accessor (derived internally)
│
├── data/
│   ├── loaders/
│   │   ├── nba_api_loader.py      # batch fetch helpers for nba_api
│   │   └── sportsdataio_loader.py # batch fetch helpers for SportsDataIO
│   ├── builders/
│   │   ├── dvp_builder.py         # builds DvP tables from raw game logs
│   │   └── player_feature_builder.py  # assembles FeatureVector per player×prop
│   └── cache/
│       ├── nba_api/               # disk cache for nba_api responses
│       ├── sportsdataio/          # disk cache for SportsDataIO responses
│       └── derived/               # disk cache for DvP and other derived data
│
├── domain/
│   ├── entities.py                # Player, Game, TeamDefense, OddsLine, etc.
│   ├── feature_vector.py          # FeatureVector dataclass (unified prop features)
│   ├── provider_models.py         # InjuryContext, SplitContext, DvPEntry, etc.
│   ├── enums.py                   # PropType, Position, DataSource, etc.
│   └── constants.py               # league averages, model weights
│
├── engine/
│   ├── slate_scanner.py           # orchestrates daily scan pipeline
│   └── prop_evaluator.py          # evaluates props from FeatureVectors
│
├── models/
│   ├── points_model.py            # points projection
│   ├── rebounds_model.py          # rebounds projection
│   ├── assists_model.py           # assists projection
│   └── ...                        # one model per prop type
│
└── odds/
    ├── implied_probability.py     # Shin + simple vig removal
    ├── fair_odds.py               # edge and Kelly calculation
    └── line_shopping.py           # best-book selection
```

---

## API Keys Required

| Provider | Key | Purpose |
|---|---|---|
| SportsDataIO | `SPORTSDATAIO_API_KEY` | Injuries, rosters, season stats, depth charts |
| The Odds API | `THE_ODDS_API_KEY` | Sportsbook prop lines and pricing |
| Sportradar | `SPORTRADAR_API_KEY` | Game schedule (slate) |
| nba_api | **none** | Usage, touches, possessions, pace, splits |

Get keys at:
- [sportsdata.io](https://sportsdata.io/)
- [the-odds-api.com](https://the-odds-api.com/)
- [developer.sportradar.com](https://developer.sportradar.com/)

---

## Feature Vector

Every prop evaluation reads from a pre-built `FeatureVector` containing:

**Base Production** — season avg, per-minute rate, projected minutes, last-5/10 rolling averages, rolling std dev  
**Usage / Tracking** — usage rate, touches, time of possession, possessions/game, pace, potential assists, rebound chances  
**Splits** — home/away factor, opponent factor, last-N trend factor  
**Injury / Role** — player injury status, teammates-out count, usage vacancy factor, role stability, starter flag  
**Matchup** — opponent defense factor, recent defense trend, opponent pace, DvP factors (pts/reb/ast/fantasy)  
**Market** — over/under odds, implied probability, best book, consensus line

---

## Supported Prop Types

`points` | `rebounds` | `assists` | `threes` | `pra` | `blocks` | `steals` | `turnovers`

---

## CLI Reference

```
python main.py [options]

  --date YYYY-MM-DD         Game date (default: today)
  --min-edge FLOAT          Minimum model edge per leg (default: 0.05)
  --max-legs INT            Max legs per parlay (default: 3)
  --min-legs INT            Min legs per parlay (default: 2)
  --stake FLOAT             Stake in $ (default: 100)
  --min-odds INT            Minimum per-leg American odds (default: -200)
  --max-odds INT            Maximum per-leg American odds (default: +400)
  --top INT                 Number of top parlays to display (default: 10)
  --prop-types [list]       Filter to specific prop types
  --sort FIELD              Ranking field: edge|confidence|combined_odds|...
  --min-confidence TIER     Minimum confidence: high|medium|low|very_low
  --verbose                 Enable debug logging
  --debug-api               Print raw API responses
```
