"""
Fit the market-anchored blend (see ``engine/market_blend.py``) on graded backtests.

Rows come from ``data/backtests/*.json``: the opportunity-only variant, over
side, graded outcome, one row per (game, player, prop, line). For every prop
type a grid over model weight ``w`` and log-odds clip ``c`` is scored with
leave-one-date-out cross-validation, so the reported improvement is measured
on dates the fit never saw. A prop type whose cross-validated Brier does not
beat the market alone gets ``w = 0`` (trust the market).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from engine.market_blend import DEFAULT_PARAMS_PATH

_DATA = Path(__file__).resolve().parents[1] / "data"
DEFAULT_SOURCES = (
    _DATA / "backtests" / "expanded_report.json",
    _DATA / "backtests" / "interaction_experiment.json",
)
TRAIN_VARIANT = "A_opportunity"
WEIGHT_GRID = tuple(round(x * 0.025, 3) for x in range(0, 21))  # 0 … 0.5 (wider grids overfit in CV)
CLIP_GRID = (0.25, 0.5, 0.75, 1.0, 1.5, math.inf)
MIN_ROWS_PER_PROP = 200
DEFAULT_WEIGHT_CAP = 0.10
_EPS = 1e-4


def load_rows(sources: Iterable[Path | str] = DEFAULT_SOURCES, variant: str = TRAIN_VARIANT) -> list[dict]:
    seen: set[tuple] = set()
    rows: list[dict] = []
    for src in sources:
        path = Path(src)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data.get("rows") or []:
            if r.get("variant") != variant or str(r.get("side", "")).lower() != "over":
                continue
            if r.get("outcome") is None or r.get("novig_probability") is None or r.get("raw_probability") is None:
                continue
            key = (r.get("game_id"), r.get("player_id"), r.get("prop_type"), r.get("line"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "date": str(r.get("game_date")),
                "prop": str(r.get("prop_type")).lower(),
                "market": float(r["novig_probability"]),
                "model": float(r["raw_probability"]),
                "y": 1.0 if r["outcome"] in (1, True, "1", "win", "hit") else 0.0,
            })
    return rows


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _blend(lm: np.ndarray, gap: np.ndarray, w: float, c: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(lm + w * np.clip(gap, -c, c))))


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(y) else float("nan")


def _best(lm, gap, y) -> tuple[float, float]:
    best = (math.inf, 0.0, math.inf)
    for c in CLIP_GRID:
        for w in WEIGHT_GRID:
            b = _brier(_blend(lm, gap, w, c), y)
            # Prefer the smaller weight / tighter clip on ties (more conservative).
            if b < best[0] - 1e-12:
                best = (b, w, c)
    return best[1], best[2]


def fit_prop(rows: list[dict]) -> dict:
    """Leave-one-date-out CV and a final full-sample fit for one group of rows."""
    dates = np.array([r["date"] for r in rows])
    market = np.array([r["market"] for r in rows])
    model = np.array([r["model"] for r in rows])
    y = np.array([r["y"] for r in rows])
    lm = _logit(market)
    gap = _logit(model) - lm

    cv_pred = np.empty_like(y)
    uniq = sorted(set(dates.tolist()))
    for d in uniq:
        test = dates == d
        train = ~test
        if not train.any():
            cv_pred[test] = market[test]
            continue
        w, c = _best(lm[train], gap[train], y[train])
        cv_pred[test] = _blend(lm[test], gap[test], w, c)

    per_date = []
    for d in uniq:
        m = dates == d
        per_date.append(_brier(cv_pred[m], y[m]) - _brier(market[m], y[m]))
    per_date_arr = np.array(per_date)
    se = float(per_date_arr.std(ddof=1) / math.sqrt(len(per_date_arr))) if len(per_date_arr) > 1 else float("nan")

    w, c = _best(lm, gap, y)
    market_brier = _brier(market, y)
    cv_brier = _brier(cv_pred, y)
    improves = cv_brier < market_brier
    if not improves:
        w = 0.0
    return {
        "weight": float(w),
        "clip": float(c) if math.isfinite(c) else 99.0,
        "n": int(len(y)),
        "n_dates": len(uniq),
        "market_brier": round(market_brier, 6),
        "model_brier": round(_brier(model, y), 6),
        "cv_blend_brier": round(cv_brier, 6),
        "cv_delta_vs_market": round(cv_brier - market_brier, 6),
        "cv_delta_date_se": round(se, 6) if math.isfinite(se) else None,
        "dates_improved": int((per_date_arr < 0).sum()),
        "cv_beats_market": bool(improves),
    }


def fit_blend(
    sources: Iterable[Path | str] = DEFAULT_SOURCES,
    out_path: Optional[Path | str] = DEFAULT_PARAMS_PATH,
) -> dict:
    rows = load_rows(sources)
    if not rows:
        raise ValueError("No graded backtest rows found; run a backtest first.")
    by_prop: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_prop[r["prop"]].append(r)

    weights: dict[str, dict] = {}
    metrics: dict[str, dict] = {}
    for prop, group in sorted(by_prop.items()):
        if len(group) < MIN_ROWS_PER_PROP:
            continue
        res = fit_prop(group)
        weights[prop] = {"weight": res["weight"], "clip": res["clip"]}
        metrics[prop] = res

    pooled = fit_prop(rows)
    metrics["all"] = pooled
    default = {"weight": min(pooled["weight"], DEFAULT_WEIGHT_CAP), "clip": pooled["clip"]}

    result = {
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "logit(p) = logit(market) + w * clip(logit(model) - logit(market), -c, c); "
                  "w, c chosen by Brier, validated leave-one-date-out",
        "train_variant": TRAIN_VARIANT,
        "n_rows": len(rows),
        "n_dates": len({r["date"] for r in rows}),
        "weights": weights,
        "default": default,
        "metrics": metrics,
    }
    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
