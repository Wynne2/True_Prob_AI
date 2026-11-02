"""
Gaussian-copula joint probability for parlays.

Each leg hits when a latent standard normal falls below ``Phi^-1(p_leg)``.
Latent correlations come from standardized residuals (actual − projected) of
graded backtest rows, per prop-type pair, split into "same player" and "same
game, different player". Different games are independent. The sign is flipped
when exactly one of the two legs is an UNDER, so an over/under pair on
positively correlated stats correctly lowers the joint probability.

This replaces the old "naive product × calibration factor × correlation
penalty" heuristic. Leg probabilities are already market-anchored, so no
extra haircut is applied here.
"""

from __future__ import annotations

import collections
import itertools
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CORR_PATH = Path(__file__).resolve().parents[1] / "data" / "models" / "prop_correlations.json"

# Priors used for pairs without a fitted value (blocks, steals, turnovers, PRA ...).
PRIOR_SAME_PLAYER = 0.20
PRIOR_SAME_GAME = 0.0
MAX_ABS_CORR = 0.85

# First fit on 19 graded dates (Jan + Feb 2026), used when no file exists.
_FALLBACK = {
    "same_player": {
        "assists|points": 0.135, "assists|rebounds": 0.179, "assists|threes": 0.043,
        "points|rebounds": 0.245, "points|threes": 0.686, "rebounds|threes": 0.055,
    },
    "same_game": {},
}


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted((str(a).lower(), str(b).lower())))


def _prop(leg) -> str:
    pt = getattr(leg, "prop_type", "")
    return str(getattr(pt, "value", pt)).lower()


def _side_sign(leg) -> int:
    side = getattr(leg, "side", "")
    return -1 if str(getattr(side, "value", side)).lower() == "under" else 1


_cache: dict[str, tuple[float, dict]] = {}


def load_correlations(path: Optional[Path | str] = None) -> dict:
    p = Path(path) if path else DEFAULT_CORR_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _FALLBACK
    hit = _cache.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        table = {
            "same_player": {k: float(v) for k, v in (data.get("same_player") or {}).items()},
            "same_game": {k: float(v) for k, v in (data.get("same_game") or {}).items()},
        }
    except Exception as exc:
        logger.warning("Correlation file %s unreadable (%s); using fallback", p, exc)
        return _FALLBACK
    _cache[str(p)] = (mtime, table)
    return table


def latent_correlation(a, b, table: Optional[dict] = None) -> float:
    """Signed latent correlation between two legs' hit events."""
    table = table or load_correlations()
    if str(a.game_id) != str(b.game_id):
        return 0.0
    key = _pair_key(_prop(a), _prop(b))
    if str(a.player_id) == str(b.player_id):
        rho = table["same_player"].get(key, PRIOR_SAME_PLAYER)
    else:
        rho = table["same_game"].get(key, PRIOR_SAME_GAME)
    rho = max(-MAX_ABS_CORR, min(MAX_ABS_CORR, rho))
    return rho * _side_sign(a) * _side_sign(b)


def _nearest_psd(m: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(m)
    if vals.min() >= 1e-8:
        return m
    vals = np.clip(vals, 1e-6, None)
    fixed = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


def joint_hit_probability(probs: Sequence[float], corr: np.ndarray) -> float:
    """P(all legs hit) under a Gaussian copula with latent correlation *corr*."""
    from scipy.stats import multivariate_normal, norm

    p = np.clip(np.asarray(probs, dtype=float), 1e-6, 1 - 1e-6)
    if len(p) == 1:
        return float(p[0])
    if np.allclose(corr, np.eye(len(p))):
        return float(np.prod(p))
    thresholds = norm.ppf(p)
    cov = _nearest_psd(np.asarray(corr, dtype=float))
    val = multivariate_normal(mean=np.zeros(len(p)), cov=cov, allow_singular=True).cdf(thresholds)
    return float(min(max(val, 0.0), float(p.min())))


def parlay_joint_probability(legs: Sequence, probs: Optional[Sequence[float]] = None) -> float:
    """Joint hit probability for parlay *legs* (objects with game_id, player_id, prop_type, side)."""
    probs = list(probs) if probs is not None else [float(l.true_probability) for l in legs]
    table = load_correlations()
    n = len(legs)
    corr = np.eye(n)
    for i, j in itertools.combinations(range(n), 2):
        corr[i, j] = corr[j, i] = latent_correlation(legs[i], legs[j], table)
    return joint_hit_probability(probs, corr)


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def fit_correlations(
    sources: Iterable[Path | str],
    out_path: Optional[Path | str] = DEFAULT_CORR_PATH,
    variant: str = "A_opportunity",
) -> dict:
    """
    Pearson correlation of per-prop standardized residuals (actual − projected).
    Same-game values within two standard errors of zero are stored as 0.
    """
    resid: dict[tuple, float] = {}
    for src in sources:
        path = Path(src)
        if not path.exists():
            continue
        for r in json.loads(path.read_text(encoding="utf-8")).get("rows") or []:
            if r.get("variant") != variant or r.get("actual") is None or r.get("projected") is None:
                continue
            key = (str(r["game_id"]), str(r["player_id"]), str(r["prop_type"]).lower())
            resid[key] = float(r["actual"]) - float(r["projected"])
    if not resid:
        raise ValueError("No graded rows with actual and projected values")

    by_prop: dict[str, list[float]] = collections.defaultdict(list)
    for (_, _, prop), v in resid.items():
        by_prop[prop].append(v)
    sd = {p: (float(np.std(v)) or 1.0) for p, v in by_prop.items()}
    z = {k: v / sd[k[2]] for k, v in resid.items()}

    same_player: dict[str, list] = collections.defaultdict(list)
    same_game: dict[str, list] = collections.defaultdict(list)
    per_game: dict[str, list] = collections.defaultdict(list)
    per_player: dict[tuple, dict] = collections.defaultdict(dict)
    for (g, pid, prop), v in z.items():
        per_game[g].append((pid, prop, v))
        per_player[(g, pid)][prop] = v
    for stats in per_player.values():
        for a, b in itertools.combinations(sorted(stats), 2):
            same_player[_pair_key(a, b)].append((stats[a], stats[b]))
    for items in per_game.values():
        for (p1, s1, v1), (p2, s2, v2) in itertools.combinations(items, 2):
            if p1 != p2:
                same_game[_pair_key(s1, s2)].append((v1, v2))

    def _corr(pairs: list) -> tuple[float, int]:
        arr = np.asarray(pairs)
        if len(arr) < 30:
            return 0.0, len(arr)
        return float(np.corrcoef(arr.T)[0, 1]), len(arr)

    out_sp, out_sg, detail = {}, {}, {"same_player": {}, "same_game": {}}
    for key, pairs in sorted(same_player.items()):
        rho, n = _corr(pairs)
        out_sp[key] = round(rho, 4)
        detail["same_player"][key] = {"rho": round(rho, 4), "n": n}
    for key, pairs in sorted(same_game.items()):
        rho, n = _corr(pairs)
        se = 1.0 / math.sqrt(max(n, 1))
        out_sg[key] = round(rho, 4) if abs(rho) > 2 * se else 0.0
        detail["same_game"][key] = {"rho": round(rho, 4), "n": n, "kept": out_sg[key] != 0.0}

    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "Pearson correlation of per-prop standardized residuals (actual - projected)",
        "variant": variant,
        "n_player_games": len(per_player),
        "same_player": out_sp,
        "same_game": out_sg,
        "detail": detail,
    }
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
