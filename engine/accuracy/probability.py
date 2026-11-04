"""Normal probabilities for the accuracy experiment. Sportsbook prices are not an input."""

from __future__ import annotations

import math

_FALLBACK_SD = {
    "minutes": 8.0,
    "points": 7.0,
    "rebounds": 2.5,
    "assists": 2.0,
    "threes": 1.2,
    "fga": 4.0,
    "fg3a": 2.0,
    "fta": 2.0,
}


def over_probability(mean: float, sd: float, line: float) -> float:
    width = max(float(sd), 0.35)
    z = (float(line) - float(mean)) / (width * math.sqrt(2.0))
    cdf = 0.5 * (1.0 + math.erf(z))
    return min(0.999, max(0.001, 1.0 - cdf))


def scale_for(scales: dict[str, float], role: str, stat: str) -> float:
    if role in scales:
        return float(scales[role])
    if "starter" in scales and role == "bench":
        return float(scales["starter"]) * 1.15
    if scales:
        return float(next(iter(scales.values())))
    return _FALLBACK_SD.get(stat, 3.0)
