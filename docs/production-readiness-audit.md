# Product and Engineering Redesign Audit

## Executive summary

The application is now positioned as a **single-page NBA player-prop research
workspace**, not a wagering execution tool. The redesign prioritizes three
outcomes: trustworthy data provenance, a guided analyst workflow, and a small
operational footprint. The primary user path is now **Overview → Straight
Bets → supporting analysis**; parlays, model evaluation, and diagnostics are
explicitly secondary.

The codebase retains its provider, domain, model, odds, and service boundaries.
Inactive integrations were removed, mutable pipeline behavior was eliminated,
and the deployment path now has locked dependencies, CI, and a server smoke
test. Live accuracy and profitability are intentionally not claimed until
timestamped forecasts are settled in the ledger.

## Inventory and decisions

| Area | Current responsibility | Decision | Rationale |
|---|---|---|---|
| `app/streamlit_app.py` | Dashboard UI and scan workflow | Keep and redesign | Streamlit is appropriate for this analyst workflow; the UI was reorganized rather than replaced with a higher-complexity frontend. |
| `main.py` | CLI workflow | Keep | Supports automation and non-browser operation. |
| `domain/` | Typed business entities and enums | Keep, progressively narrow | Provides stable contracts; `FeatureVector` is the preferred model-input boundary. |
| `providers/nba_api_provider.py`, `sportsdataio_provider.py`, `odds_api_provider.py` | Active data sources | Keep | Each has a distinct source-of-truth role. |
| `providers/sample_provider.py` | Deterministic demo data | Keep | Enables offline onboarding and deterministic testing when clearly labelled. |
| `providers/csv_import_provider.py` | Manual overrides | Keep | Retained as an explicit operator workflow. |
| Removed legacy providers | Historical unused integrations | Remove | They added audit, dependency, and maintenance surface without an active product role. |
| `data/builders/` and `services/` | Enrichment and derived context | Keep, simplify contracts | Data is useful but must remain immutable and timestamp-aware. |
| `engine/` | Scan/evaluation orchestration | Keep, split further when models expand | Current pipeline is functional; future work should split evaluator stages into independently tested pure functions. |
| `models/` | Prop projection rules | Keep, validate empirically | The models are retained as baselines; the ledger enables future evidence-based replacement. |
| `odds/` | Pricing/probability utilities | Keep | Mostly pure, testable business logic. |
| `tests/`, `.github/`, dependency files | Delivery system | Strengthen | Deterministic regression and server smoke coverage are required for production confidence. |

## Ranked findings

| Priority | Finding | Impact | Long-term decision | Risk and mitigation |
|---|---|---|---|---|
| P0 | Quote price, book, and event identity could diverge | Incorrect edge and display | Keep exact immutable quote provenance and canonical event mapping | Reject unresolved events and stale quotes. |
| P0 | Mixed-book parlay multiplication looked executable | Misleading payout | Limit to same-book theoretical combinations | Prominent UI disclosure; use actual ticket prices only when a provider supplies them. |
| P0 | TLS verification had been disabled | Data/credential interception risk | Use default certificate and hostname verification | Fail closed and surface provider failure. |
| P1 | Feature hydration mutated shared player histories | Evaluation-order-dependent forecasts | Clone evaluation inputs; preserve immutable observations | Regression tests assert source data does not change. |
| P1 | Game-specific expected minutes were not model input | Lineup information had no effect | Keep expected-minutes override distinct from season MPG | Sensitivity test guards this contract. |
| P1 | Hard calibration thresholds could reverse ordering | Misleading probability ranking | Use smooth monotonic transformation; score it historically | Do not add unvalidated manual penalties. |
| P1 | Starter records could remove bench players | Incomplete market coverage | Merge full roster, projections, and starters | Full roster is the base; richer sources overlay it. |
| P1 | Missing positional defense became team totals | Semantically invalid model feature | Preserve missingness/neutral handling | Do not substitute metrics with different units. |
| P1 | No replay or outcome scoring system | No evidence of quality/profitability | Append-only SQLite ledger with settlement and scorecard | Historical claims remain unavailable until real forecasts settle. |
| P2 | Scan/result UI was an analyst console with weak onboarding | Friction and misuse risk | Overview-first IA, mode banner, grouped controls, explicit empty states | Preserve detailed screens as secondary tabs. |
| P2 | Inactive provider modules expanded maintenance surface | Technical debt | Remove unused implementations | Restore only with a source-of-truth, contract tests, and owner. |
| P2 | Date-sensitive/skippable testing and no server smoke gate | Delivery regressions | Fixed demo mode, regression coverage, CI coverage artifact, Streamlit smoke test | Keep tests deterministic and fail on missing expected fixtures. |

## Product design decisions

### Information architecture

1. **Overview** — orientation, mode, current scan summary, and next action.
2. **Straight Bets** — primary research output.
3. **Parlays** — clearly theoretical, same-book combinations only.
4. **Prop Table** and **Line Shopping** — analyst drill-down.
5. **Model Evaluation** and **Diagnostics** — advanced/operational surfaces.

### Design system

- One primary scan action; all other controls are secondary.
- Persistent Demo/Live/Replay mode banner with plain-language implications.
- Compact metric panels and short user-facing labels.
- Summary before detail; expandable cards for evidence and explanation.
- Status colors communicate outcome, never probability certainty.
- Tables retain analyst density but use readable labels and documented truncation.

### Accessibility and responsive behavior

- Semantic Streamlit controls retain keyboard operation and native labels.
- Status is not encoded by color alone; every warning includes text.
- Sidebar controls are grouped by outcome, reducing cognitive load.
- Wide tables remain advanced views; top-level summaries use four or fewer metrics.
- Raw diagnostics are isolated from the primary workflow.

## Architecture decisions

- **Mode is explicit:** `APP_MODE=demo|live|replay`; demo data is labelled and live fallbacks are not hidden.
- **Quotes are immutable:** raw bookmaker keys and selected quote records travel with every recommendation.
- **Events are canonical:** quotes without one unambiguous slate mapping are rejected.
- **Features are immutable evidence:** observations are separate from window averages and applied to evaluation-local copies.
- **Evaluation is append-only:** raw normalized quote/input snapshots, forecasts, and settlements are preserved in SQLite.
- **Calibration is measurable:** Brier score, log loss, reliability bins, and quoted-return are available after settlement.
- **Delivery is reproducible:** exact dependency lock, Python 3.13 CI, compilation, tests, coverage artifact, and server startup smoke test.

## Prioritized roadmap

1. **Now (completed):** provenance, event matching, TLS, stale quote policy,
   same-book theoretical parlays, immutable features, expected minutes, explicit
   data modes, offline demo, ledger, and CI/smoke coverage.
2. **Next:** add provider-recorded contract fixtures, typed provider error
   results, and explicit user-facing data-health/funnel reporting.
3. **Then:** split `PropEvaluator` and `SlateScanner` into smaller pure stages,
   narrow the wide legacy `Player` entity, and make service caches injected
   dependencies rather than module globals.
4. **Only after sufficient settled history:** fit and compare chronological
   calibration models and baseline forecasts; publish reliability and
   uncertainty, not unsupported ROI claims.
5. **Later:** integrate provider-quoted parlay tickets and a validated
   joint-probability model. Until then, keep parlays theoretical.

## Measurable outcomes

- **Correctness:** quote/book/event identity, stale quote rejection, and
  non-negative theoretical ticket EV are regression-tested.
- **Reliability:** the full suite includes deterministic sample-pipeline,
  contract, regression, and browser/server smoke coverage.
- **Delivery:** CI verifies a pinned environment, dependency integrity,
  compileability, tests, coverage generation, and Streamlit boot.
- **Usability:** first load has a concrete three-step path and persistent mode
  context; filters are organized by what they affect.
- **Maintainability:** six dead provider modules were removed and service
  consumers now use public lookup APIs instead of private cache internals.
