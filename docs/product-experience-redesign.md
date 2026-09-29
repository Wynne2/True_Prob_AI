# True Prob Product Experience Redesign

## Executive summary

The prior dashboard exposed substantial analytical capability but presented
nearly every concern at the same level. A first-time user faced a dense sidebar,
seven equal-priority tabs, long tables, expandable recommendation lists, model
audit details, provider diagnostics, and theoretical parlay controls before the
product had established the primary job: identify a trustworthy pregame signal
and explain why it deserves attention.

The redesigned product is a decision-first research workspace. It introduces a
cohesive visual system, four-purpose information architecture, progressive
disclosure for advanced controls, searchable recommendation triage, clear trust
signals, polished empty/error states, responsive behavior, accessible motion,
and a dedicated performance experience. Provider and model diagnostics remain
available but no longer compete with the primary workflow.

## Audit findings and decisions

| Surface | Previous issue | Decision | Product impact | Risk and mitigation |
| --- | --- | --- | --- | --- |
| Application shell | Generic Streamlit title, flat page hierarchy, minimal identity | Replace with a branded hero, contextual status chips, and restrained premium visual language | Immediate clarity and stronger product identity | Custom CSS may be affected by future Streamlit DOM changes; native controls remain functional without it |
| Navigation | Seven equal-priority tabs mixed decisions, analysis, performance, and diagnostics | Consolidate into Home, Research, Performance, and System workspaces | Lower cognitive load and faster task completion | Existing users may initially look for old tabs; labels map directly to user intent |
| Sidebar | More than a dozen controls visible at once | Keep date, market, edge, and scan action primary; move price, display, and parlay settings into progressive disclosure | Cleaner first-run experience with fewer decisions | Advanced settings are one click deeper but retain all functionality |
| Mode/provider state | Provider details were hidden in an expander and duplicated in page copy | Show current mode and provider state beside the scan controls and in the hero | Live/demo/replay context remains continuously visible | Provider failures are still surfaced in System and scan results |
| Empty states | Generic info messages depended on users knowing what to do next | Add task-specific empty states explaining why the surface is empty and what unlocks it | Better onboarding and recovery | None; no behavior is hidden |
| Loading | Scan steps were technical and completion feedback was fragmented | Use a concise staged status flow: connect, resolve, rank, preserve | Better perceived performance and trust | Underlying synchronous Streamlit execution remains unchanged |
| Dashboard | Overview was four metrics plus one success banner | Add priority signal, market distribution, readiness panel, slate cards, and activity/exclusion views | Important information appears earlier with less hunting | Charts use existing scan data only, so no new provider dependency |
| Recommendations | Long expander labels and repeated metric grids were hard to scan | Introduce ranked opportunity cards with stable hierarchy and optional evidence drawers | Faster comparison while preserving model transparency | Custom cards are presentational; evidence stays in native accessible expanders |
| Search and triage | No way to find a player/team/market or isolate risk conditions | Add free-text search and focus filters for confidence, favorite pricing, and review-required offers | Material productivity improvement on large slates | Filtering is session-local and does not alter saved forecasts |
| Market table | Dense analyst table was the dominant representation | Keep it as a secondary Market grid with clearer labels and compact columns | Expert access without overwhelming first-time users | Full raw debug detail remains in System |
| Line shopping | Useful but visually isolated and unexplained when unavailable | Place it beside the market grid and provide explicit empty states | Better discoverability and comprehension | Equivalent quote availability remains provider-dependent |
| Parlays | Equal navigation priority implied executable live pricing | Move to a research subview and explicitly disable live presentation without provider-quoted tickets | Removes a high-risk product ambiguity | Demo/replay theoretical analysis remains available |
| Performance | Sparse metrics and a raw reliability table | Add calibration hierarchy, observed-vs-predicted chart, sample-aware empty state, and supporting table | Makes prospective validation understandable | Small samples remain clearly labelled and are not overinterpreted |
| Diagnostics | Raw API tools occupied top-level navigation | Isolate provider diagnostics, model audit, exclusions, and quote activity in System | Primary product remains focused while engineering tools stay available | Diagnostics still make explicit read-only live calls |
| Responsiveness | Fixed multi-column layouts could become cramped | Add responsive breakpoints, fluid typography, wrapping card grids, and narrower mobile spacing | Better tablet and mobile use | Streamlit sidebar behavior remains framework-controlled |
| Accessibility | Limited focus treatment, motion preference handling, and semantic hierarchy | Add visible focus rings, native form controls, high-contrast tokens, reduced-motion support, descriptive labels, and non-color status text | Stronger keyboard and low-vision usability | Formal third-party WCAG certification remains future work |
| Motion | Interface felt static, but indiscriminate animation would harm utility | Add subtle entrance, hover, and state transitions with reduced-motion override | Premium feel without distracting from evidence | Motion is limited to transform/opacity/border changes for smooth rendering |
| Performance | Schedule was loaded separately before the scanner loaded it again | Expose filtered games from `SlateScanner.last_games` and use the scanner as the single UI orchestration path | Removes one redundant schedule call per scan | Scanner behavior is covered by the existing regression suite |
| Maintainability | Visual rules and page orchestration lived in one large file | Extract design tokens and reusable presentation primitives into `app/design_system.py` | Centralized design evolution and smaller product surface | Streamlit render logic remains intentionally local to avoid a premature component framework |
| Generated cache | Live provider cache appeared as untracked repository content | Ignore `data/cache/` | Cleaner development and safer commits | Cache behavior is unchanged |

## Design system

The visual foundation uses a dark neutral canvas, layered translucent surfaces,
violet as the primary action color, cyan for secondary data emphasis, green for
positive signal, amber for uncertainty, and rose for failures. Typography uses
the local system stack to avoid font downloads and layout shifts.

Spacing, radii, borders, shadows, and motion are defined as reusable tokens.
Interactive controls use native Streamlit semantics and keyboard behavior.
Custom HTML is limited to non-interactive presentation such as hero, status
chips, matchup cards, and recommendation summaries.

Motion is intentionally short and composited: opacity and transform for entry,
transform and border color for hover, and no scroll-jacking. The
`prefers-reduced-motion` media query effectively disables transitions.

## Product architecture

### Home

Answers: What is the best current signal? How broad is the slate? Is the data
ready? What changed or was excluded?

### Research

Answers: Which recommendations match my intent? What evidence supports each
one? What equivalent prices exist? Which demo/replay theoretical combinations
qualify?

### Performance

Answers: How well calibrated are saved forecasts? What is the sample size? How
do predicted probabilities compare with observed outcomes?

### System

Answers: Is the provider contract healthy? Why was data excluded? What changed
between quote observations? What does the model audit show?

## Accessibility and responsive contract

- Native date, multiselect, slider, select, text, button, segmented-control,
  tab, expander, and table components retain keyboard semantics.
- All focusable controls receive a high-visibility cyan focus ring.
- Status is communicated with text in addition to color.
- Text and secondary text colors maintain strong contrast on the application
  background.
- Layouts collapse at 900px and 640px; recommendation metrics wrap instead of
  forcing horizontal overflow.
- Motion is reduced when the operating-system preference requests it.
- Empty states and failures provide explicit recovery guidance.

## Measurable improvements

- Top-level destinations reduced from seven tabs to four workspaces.
- Primary sidebar decisions reduced to date, market, minimum edge, and scan.
- One redundant schedule retrieval removed from every UI scan.
- Recommendation search and four focus modes added.
- Live parlay ambiguity removed from the primary navigation.
- Every no-data workspace now has a purpose-specific empty state.
- Responsive behavior is explicitly covered at two breakpoints.
- Accessibility regression tests protect focus, reduced-motion, control sizing,
  and responsive foundations.
- Browser-level Streamlit tests now cover navigation, scanning, search, empty
  results, and diagnostic separation.

## Deferred opportunities

The redesign deliberately does not add accounts, cloud synchronization,
collaboration, notifications, automated wagering, or generative explanations.
Those features introduce identity, privacy, legal, operational, or validation
requirements that are not justified by the current local single-user product.
Provider-quoted parlay pricing, richer longitudinal performance slices, and
formal assistive-technology testing are the highest-value future extensions.
