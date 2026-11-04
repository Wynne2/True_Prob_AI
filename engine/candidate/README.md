# Candidate model v1

An experimental replacement for the production projection, built and tested
apart from production. Production (Model A plus the market blend) still sets
every edge and ranking on the board. The candidate is shown next to it.

## What it is

    mean = minutes x opportunities per minute x conversion per opportunity

| Piece | How it is forecast | Code |
|---|---|---|
| Minutes | Ridge on recency (3-game half-life), rest, last season, own missed games | `minutes.py` |
| Volume | Per stat: ridge on recency, usage/touches trend, opponent pace and volume allowed, or the 20-game ratio if that did better in November (assists) | `volume.py` |
| Conversion | Shrunk recent rate (mostly noise, not modeled further) | `../accuracy/decompose.py` |
| Spread | Variance fitted around this mean: mean, mean squared, player last-10 variance, minutes volatility | `model.py` |

No sportsbook price is an input.

## How it was tested

1. Development: fit on 2024-25 + Oct 2025, choose settings on Nov 2025, check Dec 2025.
2. Freeze: refit chosen settings on all of 2024-25 and 2025-26 (`data/models/candidate_v1.json`).
3. Holdout, scored once: 2026-27 games through Nov 2 (`data/backtests/candidate_v1_holdout.json`).

## Results

December 2025 check (Brier on synthetic lines, lower is better):

| Stat | Model A chain | Candidate |
|---|---:|---:|
| Points | 0.2130 | 0.2083 |
| Rebounds | 0.2111 | 0.2036 |
| Assists | 0.2113 | 0.2093 |
| Threes | 0.2240 | 0.2108 |

2026-27 holdout, real sportsbook lines (1,185 props, 30 games, 4 dates):

| Model | Brier |
|---|---:|
| Production Model A | 0.2544 |
| Production with matchup context | 0.2536 |
| Candidate v1 | 0.2488 |
| Sportsbook no-vig | 0.2466 |

The candidate beats both production variants and closes most of the gap to
the market, but it is still behind the market overall (ahead on threes only).
On this sample the differences are about 1.5 standard errors, so they are not
yet conclusive.

## Re-running

```
python -m engine.candidate.model      # develop + freeze (only for a NEW version)
python -m engine.candidate.holdout    # score the frozen version on 2026-27 games so far
```

Do not refit v1 on holdout results. Changes go into a new version with a new
holdout window. The holdout scorer spends Odds API credits for sportsbook lines
(capped by `--credit-cap`, default 1,250).
