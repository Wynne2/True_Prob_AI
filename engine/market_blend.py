"""
Market-anchored probability blend.

The de-vigged market price is the prior; the statistical model may only move
it by a fitted, bounded amount in log-odds space:

    logit(p_final) = logit(p_market) + w * clip(logit(p_model) - logit(p_market), -c, c)

``w`` (model weight) and ``c`` (largest log-odds disagreement the model is
allowed to act on) are fitted per prop type on graded backtest rows by
``python main.py fit-blend`` (see ``engine/market_blend_fit.py``) and stored in
``data/models/market_blend.json``. The backtests showed the raw model is less
accurate than the no-vig market on its own, while a small, clipped model weight
improves on the market for points and threes. Both facts are encoded here
instead of the old hand-set haircuts.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from odds.normalizer import american_to_raw_implied_prob

logger = logging.getLogger(__name__)

DEFAULT_PARAMS_PATH = Path("data/models/market_blend.json")

# Used only when no fitted file exists. Mirrors the leave-one-date-out fit on 19 graded dates.
_FALLBACK_WEIGHTS: dict[str, tuple[float, float]] = {
    "points": (0.50, 0.5),
    "threes": (0.0, 0.5),
    "rebounds": (0.0, 0.5),
    "assists": (0.0, 0.5),
}
_FALLBACK_DEFAULT: tuple[float, float] = (0.10, 0.25)

_EPS = 1e-4


def logit(p: float) -> float:
    p = min(max(float(p), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass(frozen=True)
class BlendWeights:
    """Model weight and log-odds clip for one prop type."""
    weight: float
    clip: float


@dataclass
class BlendParams:
    """Fitted blend parameters per prop type, plus provenance."""
    by_prop: dict[str, BlendWeights]
    default: BlendWeights
    source: str = "fallback"
    fitted_at: str = ""
    metrics: dict = field(default_factory=dict)

    def for_prop(self, prop_type: object) -> BlendWeights:
        key = str(getattr(prop_type, "value", prop_type) or "").lower()
        return self.by_prop.get(key, self.default)


def fallback_params() -> BlendParams:
    return BlendParams(
        by_prop={k: BlendWeights(*v) for k, v in _FALLBACK_WEIGHTS.items()},
        default=BlendWeights(*_FALLBACK_DEFAULT),
        source="fallback",
    )


_cache: dict[str, tuple[float, BlendParams]] = {}


def load_blend_params(path: Optional[Path | str] = None) -> BlendParams:
    """Load fitted parameters (cached by file mtime); fall back to built-in defaults."""
    p = Path(path) if path else DEFAULT_PARAMS_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return fallback_params()
    cached = _cache.get(str(p))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        by_prop = {
            str(k).lower(): BlendWeights(float(v["weight"]), float(v["clip"]))
            for k, v in (data.get("weights") or {}).items()
        }
        d = data.get("default") or {}
        params = BlendParams(
            by_prop=by_prop,
            default=BlendWeights(float(d.get("weight", _FALLBACK_DEFAULT[0])), float(d.get("clip", _FALLBACK_DEFAULT[1]))),
            source=str(p),
            fitted_at=str(data.get("fitted_at") or ""),
            metrics=dict(data.get("metrics") or {}),
        )
    except Exception as exc:
        logger.warning("Market blend file %s unreadable (%s); using fallback weights", p, exc)
        return fallback_params()
    _cache[str(p)] = (mtime, params)
    return params


def blend_probability(
    model_prob: float,
    market_prob: float,
    weights: BlendWeights,
    *,
    trust: float = 1.0,
) -> float:
    """
    Move the market probability toward the model by the fitted, clipped amount.

    *trust* in [0, 1] scales the model weight down when inputs are sparse or
    the projection raised audit flags. ``trust=0`` returns the market price.
    """
    w = max(0.0, float(weights.weight)) * min(max(float(trust), 0.0), 1.0)
    lm = logit(market_prob)
    gap = logit(model_prob) - lm
    c = max(0.0, float(weights.clip))
    gap = min(max(gap, -c), c)
    return sigmoid(lm + w * gap)


def no_vig_side_probability(over_odds: int, under_odds: int, side: str) -> Optional[float]:
    """Proportional no-vig probability for *side*; ``None`` when either price is missing."""
    try:
        o, u = int(over_odds), int(under_odds)
    except (TypeError, ValueError):
        return None
    if not o or not u:
        return None
    po = american_to_raw_implied_prob(o)
    pu = american_to_raw_implied_prob(u)
    total = po + pu
    if po <= 0 or pu <= 0 or total <= 0:
        return None
    return (pu if str(side).lower() == "under" else po) / total


def consensus_market_probability(lines: Iterable, line_value: float, side: str) -> Optional[float]:
    """
    Median no-vig probability for *side* at *line_value* across every venue
    quoting both sides. A single venue's stale or one-sided quote cannot set
    the prior on its own when others exist.
    """
    probs: list[float] = []
    for ol in lines:
        try:
            if abs(float(ol.line) - float(line_value)) > 1e-9:
                continue
        except (TypeError, ValueError):
            continue
        p = no_vig_side_probability(getattr(ol, "over_odds", 0), getattr(ol, "under_odds", 0), side)
        if p is not None and 0.0 < p < 1.0:
            probs.append(p)
    if not probs:
        return None
    return float(statistics.median(probs))
