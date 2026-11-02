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
| NBA stats | BALLDONTLIE GOAT (`BALLDONTLIE_API_KEY`) |
| Config | `python-dotenv` |
| Testing | `pytest`, `pytest-cov` |

---

## Installation & Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.template .env
```

Edit `.env` and fill in your keys:

```env
BALLDONTLIE_API_KEY=    # usage, tracking, pace, season averages (or BDL_API_KEY)
THE_ODDS_API_KEY=       # sportsbook odds and prop lines (required for pricing)
```

Player usage, tracking, pace, and season averages come from **BALLDONTLIE GOAT** (`BALLDONTLIE_API_KEY` or `BDL_API_KEY`). Prop and game prices come from the **Kalshi** and **Polymarket** public market-data APIs (no key, enabled by default) plus **The Odds API** when its key is set. Set `BDL_REQUESTS_PER_MINUTE=5` on the trial tier and `600` on paid GOAT.

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
| `--min-edge` | `0.01` | Minimum edge per leg (1%; market-anchored edges are usually 1–4%) |
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

### AI Advisor (`python main.py advisor`)

Runs the model, keeps **positive-edge** props priced between **-200 and -600** (Kalshi / Polymarket lines),
dedupes to one line per player/prop/side, takes the top 5, builds correlation-checked 2- and 3-leg parlays,
and asks OpenAI (`gpt-6.1-sol` by default, Responses API with a strict JSON schema) to reason over them.

- Python computes every probability, price, payout and EV; the model may only reference IDs (`P1…`, `PL1…`).
- Numbers the model states are overwritten with the computed values; unknown IDs are rejected.
- One validation retry; after a second failure (or no key / API error) a deterministic recommendation
  (top picks + best 2-leg and 3-leg parlays by EV) is shown and the failure is stated.
- If nothing qualifies, OpenAI is not called and the filter-stage counts are printed.
- Every run is saved to `data/advisor/<date>_<HHMMSS>.json` (inputs, request metadata, raw response,
  validated output) and a matching `.md` report.

```bash
python main.py advisor                    # today, -600..-200, edge > 0, top 5, $100
python main.py advisor --stake 100 --date 2026-11-02
python main.py advisor --no-llm           # deterministic report only (no OpenAI call)
python main.py advisor --prompt "Only consider 2-leg parlays"
python main.py advisor --bankroll 1000    # add quarter-Kelly stake suggestions
python main.py advisor --help
```

| Flag | Default | Description |
|---|---|---|
| `--date` | today (ET) | Slate date |
| `--min-odds` / `--max-odds` | `-600` / `-200` | Inclusive per-leg odds band |
| `--min-edge` | `0.0` | Edge must be strictly greater |
| `--top` | `5` | Picks kept |
| `--stake` | `100` | Stake for payout math |
| `--bankroll` | none | Bankroll for fractional-Kelly stake suggestions |
| `--min-confidence` | none | Confidence floor |
| `--prompt` / `--prompt-file` | standard question | Custom question for the AI |
| `--no-llm` | off | Skip OpenAI |
| `--no-save` | off | Don't write `data/advisor/` files |

Requires `OPENAI_API_KEY` in `.env` (see Configuration). The official `openai` SDK is used when installed;
otherwise the same Responses API endpoint is called with `requests`.

### Model maintenance

```bash
python main.py fit-blend          # refit market-blend weights + parlay correlations from backtests
python main.py clv --date 2026-11-02
```

- `fit-blend` reads `data/backtests/*.json` and writes `data/models/market_blend.json` (per-prop model
  weight, chosen by date-grouped cross-validated Brier score against the no-vig market) and
  `data/models/prop_correlations.json` (residual correlations for the parlay copula).
- `clv` run **before tip-off** records closing prices for that date's ledger picks
  (`data/predictions/<date>.closing.json`, CLV = closing no-vig − price taken). Run **after games finish**
  it grades every pick against box scores (`<date>.graded.json`): hit rate, model vs market Brier score,
  flat ROI and average CLV, for all picks and positive-edge picks.

### Streamlit UI

```bash
streamlit run app/streamlit_app.py
```

Opens at `http://localhost:8501`. The sidebar exposes the same constraint controls as the CLI. The main area is split into these tabs:

| Tab | Content |
|---|---|
| **Straight Bets** | Top individual props by edge with payout calculator |
| **Parlays** | Ranked parlays with leg detail and payout breakdown |
| **Props Analysis** | Sortable DataFrame of all qualifying props |
| **Line Shopping** | Multi-book odds comparison table |
| **Game Lines** | Kalshi / Polymarket moneyline, spread and total markets |
| **🤖 AI Advisor** | Runs the AI Advisor on the current scan (stake, odds band, top N, custom question) |
| **API Debug** | Live diagnostic tool — fetches raw API responses and previews them in-browser |
| **Backtesting** | Historical calibration sample |

### Tests

```bash
pytest
pytest -m "not live"  # offline only (what CI runs); live tests hit today's slate
pytest --cov          # with coverage
```

### CI and merging to `main`

Every `git push` runs the offline suite first and is blocked if it fails. Turn it on once per clone:

```bash
git config core.hooksPath .githooks   # skip in an emergency: git push --no-verify
```

`.github/workflows/tests.yml` also runs the suite on every pull request and push to `main`. The hook is local and can be skipped, so for a hard gate the repo owner can apply `.github/rulesets/main.json` (one time, admin only), after which `main` accepts only PRs whose `pytest` check passed against the latest `main`:

```bash
# owner, once
gh api -X POST repos/Wynne2/True_Prob_AI/rulesets --input .github/rulesets/main.json
gh api -X PATCH repos/Wynne2/True_Prob_AI -F allow_auto_merge=true -F delete_branch_on_merge=true

# everyone, per change
git switch -c my-change && git push -u origin my-change
gh pr create --fill && gh pr merge --auto --squash   # merges itself when CI is green
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
│   ├── usage_tracking_service.py   # BALLDONTLIE usage/tracking
│   ├── splits_service.py           # BALLDONTLIE season averages; home/last-N left empty when the API has no split filter
│   ├── matchup_context_service.py  # Team pace, DvP context
│   ├── dvp_service.py              # Defence vs Position accessor
│   └── cache_service.py            # In-process cache wrapper
│
├── data/
│   ├── loaders/
│   │   ├── nba_api_loader.py       # Compatibility facade; fetches BALLDONTLIE
│   │   ├── balldontlie_client.py   # Auth, pagination, retries
│   │   └── sportsdataio_loader.py  # Loads from SportsDataIO with disk caching
│   └── builders/
│       ├── player_feature_builder.py  # Assembles FeatureVectors for all slate players
│       ├── dvp_builder.py             # Builds Defence-vs-Position tables
│       └── team_defense_builder.py    # Team defensive efficiency per prop type
│
├── providers/                  # Raw API client wrappers
│   ├── odds_api_provider.py
│   ├── kalshi_provider.py      # Kalshi public NBA props + game lines (no key)
│   ├── polymarket_provider.py  # Polymarket Gamma NBA props + game lines (no key)
│   ├── balldontlie_provider.py # Games, players, injuries, stats
│   ├── csv_import_provider.py
│   ├── nba_api_provider.py
│   ├── sample_provider.py      # Sample data for tests (not registered)
│   └── provider_registry.py
│
├── odds/
│   ├── implied_probability.py  # Vig removal (multiplicative normalisation)
│   ├── fair_odds.py            # Fair odds and Kelly fraction
│   ├── normalizer.py           # American ↔ decimal conversion
│   ├── parlay_math.py          # Combined parlay odds, edge, true prob
│   ├── line_shopping.py        # Multi-book best-odds selection
│   ├── prediction_market.py    # Exchange ask + taker fee → American odds
│   └── game_lines.py           # Consensus total/spread → Game enrichment
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
└── tests/                      # pytest suite
```

---

## Key Features

### Evaluation Pipeline

The `SlateScanner` runs this sequence once per daily scan — no per-prop API calls:

1. **Slate** — fetch today's games from BALLDONTLIE. Only pregame games are evaluated; in-progress and final games are excluded.
2. **Odds** — on every scan, pull every NBA player prop for the selected date from Kalshi and Polymarket (public, keyless) and The Odds API (if keyed), in parallel. Each source fails independently. Moneyline/spread/total markets from both exchanges fill each game's total, home spread, and implied team totals.
3. **Cache warm** — batch-fetch all service data: season stats, depth charts, injury lists, usage/tracking dashboards, split dashboards, team defense.
3b. **Injury news → features** — when `OPENAI_API_KEY` is set, one structured OpenAI call turns the injury feed's free-text notes into typed features for the slate date (`status_tonight`, `minutes_limit`, `confidence`). Confident status corrections override the feed and minutes limits cap the minutes model. The LLM never outputs a probability; failures fall back to the raw feed. Cached in `data/cache/news_features/`; disable with `AI_NEWS_FEATURES=0`.
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

### Market-anchored probability

Backtests show the de-vigged market price is better calibrated than the raw model, so the final
probability starts from the **consensus no-vig market price** (median across venues at the same line) and
the model may move it only by a fitted, clipped amount in log-odds:

```
logit(p) = logit(market) + w × trust × clip(logit(model) − logit(market), ±c)
```

`w` and `c` come from `data/models/market_blend.json` (`python main.py fit-blend`); `w = 0` for a prop type
means the model has not beaten the market out-of-sample, so any edge comes only from a venue pricing the
line worse than consensus. `trust` shrinks the model weight for incomplete data and audit/calibration
flags. Each prop reports `market_probability`, `model_weight`, and `probability_method`. Set
`USE_MARKET_BLEND = False` in `domain/constants.py` for the legacy calibration chain. The matchup layer is
neutralised (`MATCHUP_ADJUSTMENTS_ENABLED = False`) because opponent-defence adjustments did not improve
backtests.

### Parlay Builder

- Generates all valid leg combinations from qualifying props (up to `--max-legs`)
- Prices the joint hit probability with a **Gaussian copula** using residual correlations fitted from
  backtests (same-player points/threes ≈ 0.69, points/rebounds ≈ 0.25; cross-player ≈ 0); unders flip
  the sign. Disable with `USE_COPULA_PARLAYS = False`.
- **Kelly sizing:** picks and parlays carry a quarter-Kelly bankroll fraction (capped), shown when a
  bankroll is given.
- Blocks highly correlated leg combinations (e.g. same-player, same-game double-counting)
- Scores each parlay with a **correlation risk score** and **diversification bonus**
- Supports five ranking fields: `edge`, `confidence`, `combined_odds`, `correlation_risk`, `balanced_score`
- Tags parlays with risk profiles: `highest_edge`, `safest`, `best_balanced`, `best_odds`

### Line Shopping

Tracks over/under odds across all available sportsbooks for every prop. `best_book` and `best_book_key` on each evaluated prop identify where the best number was found.

**Supported books:** Kalshi, Polymarket, FanDuel, DraftKings, BetMGM, Caesars, PointsBet, BetRivers, Bovada, Bet365, Pinnacle, MyBookie, LowVig, BetOnline

**Prediction-market pricing:** Each Yes/No contract is priced at its best ask (Polymarket No = 1 − best bid) plus the taker fee (`rate × p × (1 − p)`; Kalshi 7%, Polymarket per-market `feeSchedule`). That fee-inclusive cost is converted to American odds, so the implied probability is the true break-even. Markets with a wide bid/ask spread or price outside 2–98¢ are skipped as untradeable. The **Game Lines** tab in the UI lists every priced moneyline, spread, and total side.

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
| `BALLDONTLIE_API_KEY` | Required for advanced stats | [docs.balldontlie.io](https://docs.balldontlie.io/) |
| `THE_ODDS_API_KEY` | Optional (adds sportsbooks) | [the-odds-api.com](https://the-odds-api.com/) |
| `OPENAI_API_KEY` | Only for `main.py advisor` AI reasoning | [platform.openai.com](https://platform.openai.com/api-keys) |

Kalshi and Polymarket need no key.

### Optional Settings

```env
# BALLDONTLIE GOAT
NBA_SEASON=2026-27          # label; API season start year is 2026
BDL_REQUESTS_PER_MINUTE=5   # trial. Paid GOAT: 600
BDL_TTL_ADVANCED=86400      # disk cache TTL for season averages (seconds)
BDL_TTL_TRACKING=86400
BDL_TTL_GAMELOGS=43200

# Odds API
ODDS_API_REGIONS=us,us2     # book regions to query (comma-separated)

# Kalshi / Polymarket (public, no key)
KALSHI_ENABLED=true
POLYMARKET_ENABLED=true
PREDICTION_MARKET_TIMEOUT=20      # seconds per request
PREDICTION_MARKET_MAX_SPREAD=0.15 # skip markets with a wider bid/ask spread
PREDICTION_MARKET_MIN_PRICE=0.02  # skip near-certain contracts
PREDICTION_MARKET_MAX_PRICE=0.98
KALSHI_FEE_RATE=0.07              # taker fee coefficient

# OpenAI (AI Advisor only)
OPENAI_MODEL=gpt-6.1-sol          # verified with GET /v1/models/{id}; never silently switched
OPENAI_REASONING_EFFORT=medium    # low | medium | high | xhigh | max
OPENAI_TIMEOUT=120                # seconds
OPENAI_MAX_OUTPUT_TOKENS=8000
AI_NEWS_FEATURES=1                # 0 = skip the injury-note → features call during scans
AI_NEWS_REASONING_EFFORT=low

# Application
LOG_LEVEL=INFO              # DEBUG | INFO | WARNING | ERROR
LOG_TO_FILE=false
LOG_FILE=nba_prop_ai.log
CACHE_TTL=300               # in-memory cache TTL in seconds (0 = disable)
DISK_CACHE_ENABLED=true     # set to false to skip disk cache entirely

# CSV import (optional overrides)
CSV_PLAYERS_PATH=data/import/players.csv
CSV_ODDS_PATH=data/import/odds.csv
CSV_DEFENSE_PATH=data/import/defense.csv
```

### Data Sources by Role

| Provider | Key required | Data supplied |
|---|---|---|
| BALLDONTLIE GOAT | Yes | Usage, tracking (potential assists, rebound chances, touches), pace, game logs, season averages |
| SportsDataIO | No | Not used on the live scan. Injuries and season stats come from BALLDONTLIE. |
| The Odds API | Yes | Prop lines, over/under pricing, multi-book odds |
| Kalshi | No | NBA player props, moneylines, spreads, totals (public market data) |
| Polymarket | No | NBA player props, moneylines, spreads, totals (Gamma API) |

---

## Troubleshooting

**The Odds API TLS or connection errors**
- Test the connection without sending your key: `curl.exe --head --max-time 10 https://api.the-odds-api.com/v4/sports`. Any HTTP response means the TLS connection was established; a missing-key response is expected.
- If the TLS handshake fails before an HTTP response, try another approved network or contact your network administrator or The Odds API. The failure can be on the network path or at the provider.
- The client verifies server certificates. If your network requires a trusted custom CA, set `REQUESTS_CA_BUNDLE` to its PEM bundle; do not disable certificate verification.

**No props found for today**
- Check provider status in the startup table. Missing keys reduce data coverage.
- Verify that games are scheduled and have not already started (only pregame matchups are evaluated).
- Use `--debug-api` to inspect raw responses from each provider.

**`UnicodeEncodeError` on Windows**
The CLI automatically reconfigures stdout/stderr to UTF-8 on Windows. If you see this in a subprocess, set `PYTHONIOENCODING=utf-8` in your environment.

**BALLDONTLIE rate limits**
Trial access is 5 requests per minute. Paid GOAT allows 600. Set `BDL_REQUESTS_PER_MINUTE` to match the key. HTTP 429 and 5xx are retried with backoff. Cached responses live under `data/cache/balldontlie/`.

**Stale data returned**
Set `DISK_CACHE_ENABLED=false` in `.env` or use the **API Debug** tab in the Streamlit UI (which bypasses disk cache automatically).

**Streamlit `zoneinfo` error**
Requires Python 3.9+. On older installs, `pip install tzdata` provides the timezone database.

---

> For informational and research purposes only. Bet responsibly. Past model performance does not guarantee future results.
