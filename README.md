# NBA Prop AI

NBA Prop AI is a local Streamlit research workspace for NBA player-prop
forecasts. It records quote provenance and model inputs, shows data-quality
exclusions, and settles saved forecasts after final statistics are available.
It does **not** execute wagers or claim profitability.

## Live-data contract

The application has one external sports-data provider:

```env
APP_MODE=live
BALLDONTLIE_API_KEY=your_key
```

| Mode | Network behavior | Intended use |
|---|---|---|
| `demo` | No external requests | Deterministic product exploration and automated tests |
| `live` | BALLDONTLIE only | Read-only pregame research with a valid key |
| `replay` | No external requests | Inspecting previously recorded ledger data |

Live mode never falls back to sample data after a credential, entitlement,
network, or rate-limit failure. A valid empty scan with an explicit reason is
preferable to invented recommendations.

The initial live market scope is **full-game points, rebounds, and assists**.
The app requires matched two-sided over/under quotes, exact bookmaker/line
identity, recent historical support, acceptable retrieval freshness, and an
availability policy before it ranks an offer. Unsupported markets, uncertain
availability, stale quotes, and unresolved player IDs are excluded and
recorded in the prediction ledger.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.template .env
```

For offline use, leave `APP_MODE=demo`. For live use, set the two values shown
above in `.env`; do not commit credentials.

## Run

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

Open `http://127.0.0.1:8501`.

The dashboard follows this flow:

1. **Overview** — mode, provider state, scan summary, top current edge.
2. **Straight Bets** — individual P/R/A research offers.
3. **Parlays** — theoretical same-book combinations only; verify an actual
   ticket price independently.
4. **Prop Table** and **Line Shopping** — detailed analyst views.
5. **Model Evaluation** — settled forecast calibration and simulated returns.
6. **Diagnostics** — redacted provider connectivity/payload inspection.

## CLI

```powershell
.\.venv\Scripts\python.exe main.py --prop-types points rebounds assists --min-edge 0.05
```

Use `--debug-api` only for local diagnostics. Captured URLs redact recognized
credential query parameters; BALLDONTLIE authentication uses an HTTP header.

## Forecast ledger and settlement

`services/evaluation_store.py` stores immutable forecast inputs, normalized
quotes, prediction time, exclusions, and append-only settlement revisions in
SQLite (`EVALUATION_DB_PATH`). Cached data is not the forecast record.

Settlement supports `win`, `loss`, `push`, `void`, and
`pending_manual_review`. Integer lines remain excluded from live ranking until
market-specific settlement rules are configured. Historical evaluation reports
Brier score, log loss, reliability bins, sample size, and simulated return;
these become meaningful only after prospective forecasts are settled.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app data domain engine models odds providers services
```

CI installs the locked environment, checks dependencies, compiles the source,
runs the regression suite with coverage, and starts a headless Streamlit smoke
server.

See [`docs/production-readiness-audit.md`](docs/production-readiness-audit.md)
for architecture decisions, operating boundaries, and the staged migration
roadmap.
