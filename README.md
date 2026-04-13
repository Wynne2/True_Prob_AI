# NBA Prop AI — True Probability Parlay Builder

A production-style NBA betting AI platform that calculates true probability
for player props, compares against live sportsbook implied probability,
finds positive-edge plays, and automatically constructs qualifying parlays
from the current day's NBA slate.

---

## Features

- **Multi-source data ingestion**: SportsDataIO, Sportradar, The Odds API, CSV import, and realistic sample data fallback
- **8 prop types**: Points, Rebounds, Assists, 3-Pointers, PRA, Blocks, Steals, Turnovers
- **Statistically rigorous projection models**: Normal, Poisson, Negative Binomial, Binomial distributions
- **Blended projection formula**: Minutes × Role × Usage × Pace × Matchup × FPA × Recent Form × Injuries
- **Fantasy Points Allowed (FPA) integration**: Integrated across all stat models as a mandatory cross-factor
- **Vig removal**: Shin (1993) method for accurate implied probability extraction
- **Live multi-book odds**: Shop lines across FanDuel, DraftKings, BetMGM, Caesars, PointsBet, BetRivers
- **Edge ranking**: True probability vs. vig-removed implied probability
- **Anti-correlation parlay engine**: Blocks correlated leg pairs (same player/stat overlaps, PRA components)
- **Parlay constraints**: Min edge, max legs, odds range, confidence tier, prop type filter
- **Bankroll calculator**: Stake → payout → profit with Kelly fraction suggestion
- **Streamlit dashboard**: Interactive UI for daily use
- **Rich CLI**: Quick terminal workflow
- **Graceful degradation**: Missing API keys → automatic sample data fallback, no crashes

---

## Quick Start (No API Keys Required)

```bash
# Clone and install
git clone <your-repo>
cd True_Prob_AI
pip install -r requirements.txt

# Run the Streamlit dashboard (uses sample data automatically)
python -m streamlit run app/streamlit_app.py

# Or use the CLI
python main.py

# CLI with custom constraints
python main.py --min-edge 0.06 --max-legs 2 --stake 200

# Run tests
python -m pytest tests/ -v
```

> **Windows note**: If `streamlit` or `pytest` are not recognized as commands, use
> `python -m streamlit run app/streamlit_app.py` and `python -m pytest tests/ -v`.
> This happens when the Python Scripts folder is not on PATH (common with the
> Microsoft Store Python install).

The platform runs immediately with rich sample data — no API keys needed.

---

## Project Structure

```
True_Prob_AI/
├── main.py                    # CLI entry point (Rich console output)
├── config.py                  # Env var management + provider credentials
├── requirements.txt
├── .env.template              # Copy to .env and fill your API keys
├── README.md
│
├── app/
│   └── streamlit_app.py       # Full interactive Streamlit dashboard
│
├── domain/                    # Core types (no external dependencies)
│   ├── entities.py            # Player, Game, Prop, OddsLine, Parlay dataclasses
│   ├── enums.py               # PropType, ConfidenceTier, BookName, etc.
│   ├── constants.py           # Weights, thresholds, distribution params
│   └── schemas.py             # Pydantic schemas for cross-provider validation
│
├── providers/                 # Data source adapters
│   ├── base_provider.py       # Abstract interface
│   ├── sample_provider.py     # Always-available fallback (built-in)
│   ├── sportsdataio_provider.py
│   ├── sportradar_provider.py
│   ├── odds_api_provider.py
│   ├── fantasypros_provider.py
│   ├── nba_official_provider.py
│   ├── statmuse_provider.py
│   ├── rotogrinders_provider.py  # Stub (no public API)
│   ├── rotowire_provider.py      # Stub (no public API)
│   ├── csv_import_provider.py    # CSV/manual import
│   └── provider_registry.py      # Priority chain + fallback routing
│
├── data/
│   ├── sample_players.py      # 18 realistic NBA player profiles
│   ├── sample_teams.py        # 14-team defensive profiles + FPA
│   ├── sample_games.py        # 7-game sample slate + multi-book odds
│   ├── loaders.py             # Provider-first data loading with fallback
│   └── normalizers.py         # Raw API dict → domain entity conversion
│
├── models/                    # Stat projection models
│   ├── base_model.py          # Abstract base with shared adjustment helpers
│   ├── points_model.py        # Normal distribution
│   ├── rebounds_model.py      # Negative Binomial
│   ├── assists_model.py       # Negative Binomial
│   ├── threes_model.py        # Binomial (attempts × make rate)
│   ├── pra_model.py           # Normal with covariance inflation
│   ├── blocks_model.py        # Poisson
│   ├── steals_model.py        # Poisson
│   ├── turnovers_model.py     # Negative Binomial
│   ├── matchup_model.py       # Positional defense multipliers
│   ├── minutes_model.py       # Expected minutes (role + injury + B2B)
│   ├── usage_model.py         # Effective usage rate (injury adjustment)
│   ├── variance_model.py      # Game-to-game std + consistency score
│   ├── fantasy_points_allowed_model.py   # FPA factor
│   └── confidence_model.py    # ConfidenceTier (HIGH/MEDIUM/LOW/VERY_LOW)
│
├── odds/
│   ├── normalizer.py          # American ↔ Decimal ↔ Fractional conversion
│   ├── implied_probability.py # Vig removal: Shin (default) + simple
│   ├── fair_odds.py           # Fair odds, edge, EV, Kelly
│   ├── line_shopping.py       # Best book per prop side
│   └── parlay_math.py         # Combined odds, combined probs, payout
│
├── engine/
│   ├── prop_evaluator.py      # Core: project → prob → edge pipeline
│   ├── slate_scanner.py       # Scan all games for a date
│   ├── parlay_builder.py      # Combinatorial parlay generation
│   ├── correlation_engine.py  # Pairwise correlation + blocking rules
│   ├── ranking_engine.py      # Sort + risk profile tagging
│   ├── explanation_engine.py  # Human-readable leg explanations
│   └── bankroll_engine.py     # Stake → payout → profit + Kelly
│
├── utils/
│   ├── distributions.py       # scipy distribution wrappers
│   ├── math_helpers.py        # Clamp, weighted avg, Kelly, etc.
│   ├── formatting.py          # American odds display, currency
│   ├── logging_utils.py       # Structured logging setup
│   └── date_utils.py          # Today's date, season detection
│
└── tests/
    ├── test_odds.py
    ├── test_distributions.py
    ├── test_providers.py
    ├── test_prop_evaluator.py
    ├── test_parlay_builder.py
    └── test_profit_calculator.py
```

---

## Environment Variables

Copy `.env.template` to `.env` and fill in your API keys:

```bash
cp .env.template .env
```

| Variable | Provider | Required? |
|---|---|---|
| `SPORTSDATAIO_API_KEY` | SportsDataIO | Optional (recommended) |
| `SPORTRADAR_API_KEY` | Sportradar | Optional |
| `THE_ODDS_API_KEY` | The Odds API | Optional (recommended for live odds) |
| `FANTASYPROS_API_KEY` | FantasyPros | Optional |
| `NBA_OFFICIAL_API_KEY` | NBA Official | Optional |
| `STATMUSE_API_KEY` | StatMuse | Optional |
| `ROTOGRINDERS_API_KEY` | RotoGrinders | Not yet (no public API) |
| `ROTOWIRE_API_KEY` | RotoWire | Not yet (no public API) |

**All keys are optional.** Missing keys cause that provider to be skipped;
the system automatically falls back to the next provider in the chain, and
ultimately to built-in sample data.

---

## CLI Usage

```bash
# Default: today's slate, 5% min edge, max 3 legs, $100 stake
python main.py

# Custom constraints
python main.py \
  --min-edge 0.07 \
  --max-legs 3 \
  --min-legs 2 \
  --stake 250 \
  --min-odds -150 \
  --max-odds 350 \
  --min-confidence medium \
  --top 15

# Filter to specific prop types
python main.py --prop-types points rebounds assists

# Specific date
python main.py --date 2025-04-15

# Sort by balanced score instead of edge
python main.py --sort balanced_score

# Verbose logging
python main.py --verbose
```

---

## Streamlit Dashboard

```bash
python -m streamlit run app/streamlit_app.py
```

Features:
- Date selector (defaults to today)
- Sidebar controls for all constraints (edge, legs, odds range, prop types, stake)
- **Parlays tab**: Ranked parlays with leg-level breakdown, payout/profit metrics, risk tags
- **Props Analysis tab**: Full sortable table of all evaluated props with edge highlighting
- **Line Shopping tab**: Multi-book odds comparison table

---

## Provider Setup

### SportsDataIO (Recommended)

1. Sign up at [sportsdata.io](https://sportsdata.io/)
2. Subscribe to NBA plan (schedules + injuries + projections + odds)
3. Set `SPORTSDATAIO_API_KEY=your_key` in `.env`

### The Odds API (Recommended for Live Odds)

1. Sign up at [the-odds-api.com](https://the-odds-api.com/)
2. Free tier supports NBA player props
3. Set `THE_ODDS_API_KEY=your_key` in `.env`

### Sportradar

1. Sign up at [developer.sportradar.com](https://developer.sportradar.com/)
2. Trial access available; production access requires agreement
3. Set `SPORTRADAR_API_KEY=your_key` in `.env`

### CSV Import

Drop structured CSV files at the configured paths:
- `data/import/players.csv` — player stats
- `data/import/odds.csv` — odds lines
- `data/import/defense.csv` — team defensive profiles

See `providers/csv_import_provider.py` for column schemas.

---

## How the Model Works

### Projection Formula

```
projected_stat = base_season_avg
              × minutes_factor        (expected minutes / season avg minutes)
              × usage_factor          (effective usage rate)
              × pace_factor           (opponent pace vs league avg, sensitivity-weighted)
              × matchup_factor        (opponent def. efficiency × positional defense)
              × fpa_factor            (opponent FPA vs position vs league avg)
              × recent_form_factor    (blended L5/L10 vs season avg)
              × injury_factor         (player + teammate injury adjustment)
              × home_away_factor      (player's split vs season avg)
```

### Distribution Mapping

| Prop | Distribution | Parameters |
|---|---|---|
| Points | Normal | mean = projection, std = historical + inflation |
| PRA | Normal | mean = pts+reb+ast, std = combined + covariance inflation (1.12×) |
| Rebounds | Negative Binomial | mean = projection, variance = mean × 1.20 |
| Assists | Negative Binomial | mean = projection, variance = mean × 1.25 |
| Turnovers | Negative Binomial | mean = projection, variance = mean × 1.25 |
| Blocks | Poisson | λ = projection |
| Steals | Poisson | λ = projection |
| 3-Pointers | Binomial | n = projected attempts, p = 3P% |

### Edge Calculation

```python
true_probability = P(stat > line)    # from model distribution CDF
implied_probability = vig_removed(over_odds, under_odds)  # Shin method
edge = true_probability - implied_probability
```

### Parlay Anti-Correlation Rules

| Pair Type | Correlation | Action |
|---|---|---|
| Same player + same stat (or PRA overlap) | 1.00 | **BLOCKED** |
| Same player + different stats | 0.55 | Penalised |
| Same game + same team, different players | 0.40 | Penalised |
| Same game, different teams | 0.15 | Minor penalty |
| Different games | 0.05 | Diversification bonus |

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=. --cov-report=term-missing

# Run specific suite
python -m pytest tests/test_odds.py -v
python -m pytest tests/test_parlay_builder.py -v
```

---

## Extending the Platform

### Adding a New Provider

1. Create `providers/my_provider.py` subclassing `BaseProvider`
2. Implement only the methods your API supports
3. Set `source_name = DataSource.MY_PROVIDER` (add to `DataSource` enum)
4. Add a credential check in `provider_registry.py → _build_providers()`

### Adding a New Prop Type

1. Add value to `PropType` enum in `domain/enums.py`
2. Create `models/my_prop_model.py` subclassing `BaseStatModel`
3. Register it in `engine/prop_evaluator.py → _MODELS` dict
4. Add distribution helpers to `utils/distributions.py` if needed
5. Add correlation rules to `engine/correlation_engine.py`

---

## Disclaimer

This platform is built for informational, research, and educational purposes.
Betting involves financial risk. Past model performance does not guarantee
future results. Bet responsibly and within your means. Verify all information
with official sources before placing any wager.
