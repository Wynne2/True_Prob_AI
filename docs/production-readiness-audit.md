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

## Architecture audit and trim (September 2026)

The in-place audit traced the Streamlit and CLI entrypoints through the
scanner, provider registry, BALLDONTLIE adapter/client, projection engine,
and SQLite forecast ledger. The priorities and changes were:

| Priority | Finding | Change | Remaining risk |
| --- | --- | --- | --- |
| High | Registry converted credential, transport and entitlement errors into empty lists, indistinguishable from an empty slate. | Preserve explicit provider failures through UI/CLI boundaries; an injury-feed failure stops participant resolution rather than silently continuing with missing availability. | Real entitlement and payload behavior still needs validation with a licensed account. |
| High | Scanner erased its player feature map just before writing forecasts; source inputs were stored as JSON `null`. Ledger write errors were only logged. | Save the actual pregame `Player` inputs and exclusions, including scans with zero accepted offers; propagate ledger failures. | SQLite is still a local single-writer solution. |
| High | Forecast identity ignored model-input changes; a revised projection could be deduplicated away. | Include serialized features, exact quote, and probabilities in the decision identity. Existing rows remain untouched. | A newly fetched quote is a new observation; high scan cadence grows the ledger. |
| Medium | UI rebuilt the provider client each scan, bypassing its per-client request budget, while quote activity was lost on every new scanner instance. | Retain the registry/client and scanner across session scans; share the per-account in-process request budget between clients. | Multiple server processes still need a distributed budget owner. |
| Medium | Cached responses could be reused across credentials or configured API hosts. | Scope cache namespaces by an irreversible account/host digest. | Old ignored cache files remain inert until cleaned by normal housekeeping. |
| Medium | Diagnostic tools monkey-patched global `requests` and process environment from concurrent Streamlit sessions, while CLI debug did not observe session-based requests. | Replace with bounded, uncached direct diagnostic calls; remove monkey-patching and restrict diagnostics to live mode. | The diagnostic probe samples one game's props rather than all games. |
| Low | The old cross-provider normalizer/schema, vacancy calculator, broad provider interface/loaders, and CSV/logging defaults had no production consumers. Some defaults could infer active starters and unknown books. | Remove dead modules and related tests, narrow the provider contract, remove obsolete settings and unused Pydantic dependency. | Unsupported historical integrations would need explicit adapters before reintroduction. |
| Low | One-off heavy-favorite debug hook wrote a local log from within projection, swallowing I/O errors. | Remove the side effect; retain the in-memory favorite-band audit output. | No change to model calculations. |

We intentionally retained the non-P/R/A stat models and the parlay engine for
demo/replay compatibility, instead of removing tested behavior from the
product. The unused advanced-defense data path remains inactive in live mode
until a validated single-provider contract exists.
