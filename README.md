# NBA Prop AI — True Probability Parlay Builder

Estimates the **true probability** of NBA player prop outcomes, compares it against the sportsbook-implied probability, and surfaces positive-edge plays. Valid edges are assembled into ranked parlays with correlation protection, payout calculation, and line shopping across major books.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Data / stats | `pandas`, `numpy`, `scipy` |
| Domain models | `pydantic` |
| Terminal UI | `rich` |
| Web UI | `streamlit` |
| HTTP | `requests` |
| NBA stats (free) | `nba_api` |
| Config | `python-dotenv` |
| Testing | `pytest`, `pytest-cov` |

---

## Installation & Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

For a reproducible validated environment, use `pip install -r requirements.lock`.

### 2. Configure API keys

```bash
cp .env.template .env
```

Edit `.env` and fill in your keys:

```env
SPORTSDATAIO_API_KEY=   # injuries, rosters, season stats, depth charts
THE_ODDS_API_KEY=       # sportsbook odds and prop lines (required for pricing)
SPORTRADAR_API_KEY=     # game schedule / slate (primary source)
```

`nba_api` uses public NBA.com endpoints and **requires no key**.

Set `APP_MODE=live` after configuring providers. With no `.env` setting, the
application starts in clearly labelled `demo` mode using deterministic sample
data; it is not live pricing and must not be used as a wagering recommendation.

### 3. Verify provider status

Both the CLI and Streamlit UI display a provider status table on startup showing which keys are active and which are missing.

---

## Usage

### CLI

```bash
# Default run — today's slate, all prop types, default constraints
python main.py

# Common options
python main.py --min-edge 0.07 --max-legs 2 --stake 200
python main.py --date 2025-04-14 --prop-types points assists
python main.py --prop-types points rebounds --sort balanced_score --top 20
python main.py --min-odds -150 --max-odds +300 --min-confidence medium
python main.py --playoff                    # boost minute projections for playoff rotations
python main.py --debug-api                  # print raw JSON from every HTTP call
python main.py --verbose                    # DEBUG-level log output
```

#### All CLI flags

| Flag | Default | Description |
|---|---|---|
| `--date` | today (ET) | Game date `YYYY-MM-DD` |
| `--min-edge` | `0.05` | Minimum edge per leg (5%) |
| `--max-legs` | `3` | Maximum legs per parlay |
| `--min-legs` | `2` | Minimum legs per parlay |
| `--stake` | `100` | Wager amount in USD |
| `--min-odds` | `-200` | Minimum per-leg American odds |
| `--max-odds` | `+400` | Maximum per-leg American odds |
| `--min-parlay-odds` | `-10000` | Minimum combined parlay odds |
| `--max-parlay-odds` | `100000` | Maximum combined parlay odds |
| `--top` | `10` | Number of parlays to display |
| `--prop-types` | all | One or more: `points rebounds assists threes pra blocks steals turnovers` |
| `--sort` | `edge` | Parlay ranking: `edge`, `confidence`, `combined_odds`, `correlation_risk`, `balanced_score` |
| `--min-confidence` | none | Filter legs: `high`, `medium`, `low`, `very_low` |
| `--playoff` | off | Enable playoff minute-projection boost |
| `--debug-api` | off | Print redacted raw API responses |
| `--verbose` | off | Enable DEBUG logging |

### Streamlit UI

```bash
streamlit run app/streamlit_app.py
```

Opens at `http://localhost:8501`. The sidebar separates run controls, bet filters, parlay filters, and display preferences. The main area is organized around an overview-first workflow:

| Tab | Content |
|---|---|
| **Overview** | Scan status, top current edge, key counts, and the next recommended action |
| **Straight Bets** | Top individual props by edge with payout calculator |
| **Parlays** | Ranked same-book theoretical parlays with leg detail and payout breakdown |
| **Prop Table** | Sortable DataFrame of all qualifying props |
| **Line Shopping** | Multi-book odds comparison table |
| **Model Evaluation** | Brier score, log loss, reliability, and quoted-return reporting for settled forecasts |
| **Diagnostics** | Live provider connectivity and redacted raw-response inspection |
| **Evaluation** | Brier score, log loss, quoted-return and reliability bins for settled forecasts |

### Tests

```bash
pytest
pytest --cov          # with coverage
```

---

## Project Structure

```
True_Prob_AI/
├── main.py                     # CLI entry point
├── config.py                   # Env vars, provider credentials, all config objects
├── requirements.txt
├── .env.template
│
├── app/
│   └── streamlit_app.py        # Streamlit dashboard
│
├── domain/
│   ├── entities.py             # Core data classes: Player, Game, PropProbability, Parlay, …
│   ├── enums.py                # PropType, ConfidenceTier, BookName, SortField, …
│   ├── feature_vector.py       # FeatureVector — unified per-player-prop feature object
│   ├── constants.py            # Tunable weights, thresholds, season constants
│   ├── schemas.py              # Pydantic schemas for provider responses
│   └── provider_models.py      # Provider-layer data models
│
├── engine/
│   ├── slate_scanner.py        # Main orchestrator — runs the full daily pipeline
│   ├── prop_evaluator.py       # Per-prop evaluation: projection → true prob → edge
│   ├── parlay_builder.py       # Generates and filters valid parlays
│   ├── ranking_engine.py       # Sorts parlays; assigns risk-profile tags
│   ├── bankroll_engine.py      # Payout and Kelly fraction calculation
│   ├── correlation_engine.py   # Correlation risk scoring and anti-correlation blocking
│   ├── market_calibration.py   # Market-based probability calibration
│   ├── final_calibration_gate.py  # Final sanity gate before edge is accepted
│   └── explanation_engine.py   # Generates human-readable leg explanations
│
├── models/                     # One stat model per prop type
│   ├── points_model.py
│   ├── rebounds_model.py
│   ├── assists_model.py
│   ├── threes_model.py
│   ├── pra_model.py
│   ├── blocks_model.py
│   ├── steals_model.py
│   ├── turnovers_model.py
│   ├── minutes_model.py
│   ├── usage_model.py
│   ├── matchup_model.py
│   ├── injury_redistribution_model.py
│   ├── confidence_model.py
│   ├── variance_model.py
│   ├── projection_baseline.py  # Stable baseline before matchup/pace adjustments
│   ├── projection_audit.py
│   └── projection_guards.py    # Projection sanity clamps
│
├── services/                   # Service layer — data enrichment and context
│   ├── player_context_service.py   # Season stats, roster, depth chart
│   ├── injury_context_service.py   # Injury list, lineup projections
│   ├── usage_tracking_service.py   # nba_api usage/tracking dashboard
│   ├── splits_service.py           # nba_api split context dashboards
│   ├── matchup_context_service.py  # Team pace, DvP context
│   ├── dvp_service.py              # Defence vs Position accessor
│   └── cache_service.py            # In-process cache wrapper
│
├── data/
│   ├── loaders/
│   │   ├── nba_api_loader.py       # Loads from nba_api with disk caching
│   │   └── sportsdataio_loader.py  # Loads from SportsDataIO with disk caching
│   └── builders/
│       ├── player_feature_builder.py  # Assembles FeatureVectors for all slate players
│       ├── dvp_builder.py             # Builds Defence-vs-Position tables
│       └── team_defense_builder.py    # Team defensive efficiency per prop type
│
├── providers/                  # Raw API client wrappers
│   ├── odds_api_provider.py
│   ├── sportradar_provider.py
│   ├── sportsdataio_provider.py
│   ├── nba_api_provider.py
│   ├── sample_provider.py      # Fallback sample data (no keys required)
│   └── provider_registry.py
│
├── odds/
│   ├── implied_probability.py  # Vig removal (multiplicative normalisation)
│   ├── fair_odds.py            # Fair odds and Kelly fraction
│   ├── normalizer.py           # American ↔ decimal conversion
│   ├── parlay_math.py          # Combined parlay odds, edge, true prob
│   └── line_shopping.py        # Multi-book best-odds selection
│
├── utils/
│   ├── date_utils.py           # Eastern-timezone date helpers, pregame filter
│   ├── formatting.py           # American odds, currency, edge/prob formatting
│   ├── api_debug.py            # API response capture (CLI debug + Streamlit tab)
│   ├── distributions.py        # Normal, Poisson, Negative Binomial CDF helpers
│   ├── feature_validator.py    # FeatureVector completeness checks
│   ├── logging_utils.py
│   └── math_helpers.py
│
└── tests/                      # 11 test modules
```

---

## Key Features

### Evaluation Pipeline

The `SlateScanner` runs this sequence once per daily scan — no per-prop API calls:

1. **Slate** — fetch today's games via Sportradar (fallback: SportsDataIO). Only pregame games are evaluated; in-progress and final games are excluded.
2. **Odds** — pull all prop markets from The Odds API (only source for sportsbook pricing).
3. **Cache warm** — batch-fetch all service data: season stats, depth charts, injury lists, usage/tracking dashboards, split dashboards, team defense.
4. **Player build** — construct `Player` entities from `PlayerContextService`.
5. **Feature vectors** — `PlayerFeatureBuilder` assembles a `FeatureVector` per player × prop type from all service outputs.
6. **Evaluate** — `PropEvaluator` runs the stat model, converts projection to true probability via the appropriate distribution, computes vig-removed implied probability, calculates edge, assigns confidence tier, and generates an explanation.

### Stat Models

Each prop type has a dedicated model. Projections blend multiple signals:

| Signal | Weight |
|---|---|
| Season average | 35% |
| Last-10 game average | 30% |
| Last-5 game average | 20% |
| Matchup adjustment | 10% |
| Recent trend | 5% |

Additional adjustments: pace factor, Defence-vs-Position (DvP), injury redistribution (positional similarity weights), usage-rate scaling, and minute projection.

### Probability → Distribution

| Prop type | Distribution |
|---|---|
| Points, PRA | Normal |
| Rebounds, Assists | Normal or Negative Binomial |
| Blocks, Steals, Turnovers, Threes | Poisson or Negative Binomial |

A probability shrinkage factor and floor/ceiling clamps prevent extreme outputs.

### Parlay Builder

- Generates all valid leg combinations from qualifying props (up to `--max-legs`)
- Restricts every candidate to quotes available at one exact provider bookmaker; it never multiplies best singles from different books
- Labels combined prices as theoretical until a provider returns an actual parlay-ticket quote
- Filters candidates whose final adjusted expected return is not positive
- Blocks highly correlated leg combinations (e.g. same-player, same-game double-counting)
- Scores each parlay with a **correlation risk score** and **diversification bonus**
- Supports five ranking fields: `edge`, `confidence`, `combined_odds`, `correlation_risk`, `balanced_score`
- Tags parlays with risk profiles: `highest_edge`, `safest`, `best_balanced`, `best_odds`

### Line Shopping

Tracks over/under odds across all available sportsbooks for every prop. `best_book` and `best_book_key` on each evaluated prop identify where the best number was found.

**Supported books:** FanDuel, DraftKings, BetMGM, Caesars, PointsBet, BetRivers, Bovada, Bet365, Pinnacle, MyBookie, LowVig, BetOnline

### Confidence Tiers

| Tier | Meaning |
|---|---|
| `high` | Multiple strong signals align |
| `medium` | Moderate signal alignment |
| `low` | Weak or conflicting signals |
| `very_low` | Insufficient data or high variance |

### Playoff Mode

Pass `--playoff` (CLI) or check **Playoff slate** (UI) to apply a small upward adjustment to projected minutes, reflecting tighter playoff rotations and higher average playing time for key contributors.

---

## Configuration

All configuration is loaded from `.env` (or environment variables). Copy `.env.template` to `.env` to get started.

### API Keys

| Variable | Required | Source |
|---|---|---|
| `SPORTSDATAIO_API_KEY` | Recommended | [sportsdata.io](https://sportsdata.io/) |
| `THE_ODDS_API_KEY` | Required for odds | [the-odds-api.com](https://the-odds-api.com/) |
| `SPORTRADAR_API_KEY` | Recommended | [developer.sportradar.com](https://developer.sportradar.com/) |

### Optional Settings

```env
# nba_api
NBA_SEASON=2025-26          # season string (default: 2025-26)
NBA_API_SLEEP=0.6           # rate-limit delay in seconds between requests
NBA_API_TTL_ADVANCED=86400  # disk cache TTL for advanced dashboard (seconds)
NBA_API_TTL_TRACKING=86400  # disk cache TTL for tracking dashboard (seconds)
NBA_API_TTL_GAMELOGS=43200  # disk cache TTL for game logs (seconds)
NBA_API_TTL_SPLITS=86400    # disk cache TTL for split dashboards (seconds)

# Odds API
ODDS_API_REGIONS=us,us2     # book regions to query (comma-separated)

# Application
APP_MODE=live              # live | demo | replay; demo data is never live pricing
LOG_LEVEL=INFO              # DEBUG | INFO | WARNING | ERROR
LOG_TO_FILE=false
LOG_FILE=nba_prop_ai.log
CACHE_TTL=300               # in-memory cache TTL in seconds (0 = disable)
DISK_CACHE_ENABLED=true     # set to false to skip disk cache entirely
EVALUATION_DB_PATH=data/evaluation/forecast_ledger.sqlite3
MAX_QUOTE_AGE_SECONDS=900  # reject stale provider-updated odds before ranking

# CSV import (optional overrides)
CSV_PLAYERS_PATH=data/import/players.csv
CSV_ODDS_PATH=data/import/odds.csv
CSV_DEFENSE_PATH=data/import/defense.csv
```

### Data Sources by Role

| Provider | Key required | Data supplied |
|---|---|---|
| `nba_api` | No | Usage rate, touches, pace, advanced stats, game logs, splits |
| SportsDataIO | Yes | Injuries, rosters, depth charts, season stats |
| Sportradar | Yes | Game slate (primary schedule source) |
| The Odds API | Yes | Prop lines, over/under pricing, multi-book odds |

---

## Troubleshooting

**No props found for today**
- Check provider status in the startup table. Missing keys reduce data coverage.
- Verify that games are scheduled and have not already started (only pregame matchups are evaluated).
- Use `--debug-api` to inspect raw responses from each provider.

**`UnicodeEncodeError` on Windows**
The CLI automatically reconfigures stdout/stderr to UTF-8 on Windows. If you see this in a subprocess, set `PYTHONIOENCODING=utf-8` in your environment.

**nba_api rate-limit errors**
Increase `NBA_API_SLEEP` in `.env` (default `0.6` seconds). The data layer calls nba_api in batches, not per-prop, to minimise total requests.

**Stale data returned**
Set `DISK_CACHE_ENABLED=false` in `.env` or use the **API Debug** tab in the Streamlit UI (which bypasses disk cache automatically).

**Streamlit `zoneinfo` error**
Requires Python 3.9+. On older installs, `pip install tzdata` provides the timezone database.

---

> For informational and research purposes only. Bet responsibly. Past model performance does not guarantee future results.
