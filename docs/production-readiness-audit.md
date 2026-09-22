# BALLDONTLIE-only migration

## Decision

This application has exactly one live sports-data provider:
**BALLDONTLIE**. It retains Streamlit, the existing numerical model layer, the
disk cache, and the SQLite prediction ledger. It does not scrape, use a second
provider, call an LLM in the numerical path, or execute wagers.

## Delivered phases

| Phase | Delivery |
|---|---|
| Baseline | `3a71691` was reconciled with public baseline `080bd6f`; migration work is on `balldontlie-migration`. The inherited baseline passed before migration. |
| Configuration | `BALLDONTLIE_API_KEY` is the only live credential. Demo/replay issue no external provider requests. |
| Client and adapter | One TLS-verified session centralizes authorization, request budget, cursor pagination, retry/backoff, disk cache, and provider error codes. |
| Pregame pipeline | Schedule, active roster membership, quoted player IDs, availability observations, completed historical logs, and P/R/A props originate from BALLDONTLIE. Target-game box scores are never used to discover participants. |
| Prediction contracts | Unknown/questionable availability is an explicit exclusion; missing starter evidence cannot trigger starter rules or the 28-minute floor. Integer lines and unsupported markets are excluded. |
| Ledger and UI | Immutable prediction inputs, normalized quotes, exclusions, append-only settlement revisions, freshness/quality flags, and an Updates feed are visible or persisted. |
| Cleanup | Legacy data providers, direct NBA.com/SportsDataIO loaders, CSV fallback, and the `nba_api` dependency were removed. |

## Live acceptance contract

1. Live mode requires `BALLDONTLIE_API_KEY`; failure never switches to demo.
2. Live recommendations are limited to full-game points, rebounds, and assists.
3. A recommendation requires exact game/player/book/line/side identity, two
   valid price sides, quote retrieval freshness, historical support, and an
   accepted availability policy.
4. Source update time and retrieval time are stored independently. Missing
   source update time is a quality flag, not a fabricated timestamp.
5. Every forecast has immutable input/quote JSON and a decision timestamp.
   Cache expiration cannot erase it.
6. Settlement writes revisions rather than replacing forecast history. A
   corrected result produces a later settlement revision.

## Deferred until evidence exists

- Current lineup confirmation: BALLDONTLIE lineups arrive after game start, so
  it is not represented as a pregame starter signal.
- Positional defense/advanced tracking: disabled until they have a validated
  same-provider, same-unit contract.
- Additional markets: threes, blocks, steals, turnovers, PRA, and any
  non-full-game market remain outside the initial rollout.
- Parlays: disabled in live mode until actual ticket pricing and validated
  dependence estimates are available.
- Calibration/profitability claims: model evaluation is populated only by
  prospective predictions settled from final results.

## Operational metrics

Monitor requested/returned games and markets, matched/unresolved players,
accepted/excluded props, provider request budget, throttles, scan duration,
quote retrieval age, source-time coverage, and settlement completeness.
Provider health and model quality are separate measurements.
