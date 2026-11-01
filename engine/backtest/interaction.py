"""
Opportunity × matchup experiment.

Coefficients are fit on the training window only. A zero interaction is an
allowed result. Production matchup weights are not read or written here.
"""

from __future__ import annotations

from collections import defaultdict

from engine.backtest.asof import logs_before
from engine.backtest.profiles import position_bucket
from utils.math_helpers import clamp

# Prespecified. Not chosen from February or March.
MODEL_D_STRENGTH = 0.25
SHRINK_GAMES = 20.0
# Broad BALLDONTLIE positions are mapped onto five slots. That mapping is uncertain.
MAPPING_CONFIDENCE = {
    "PG": 0.55,  # almost all "G" rows
    "SG": 0.45,  # hybrids only
    "SF": 0.55,  # almost all "F" rows
    "PF": 0.45,  # hybrids only
    "C": 0.85,
}
LAMBDA_GRID = (0.0, 1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0)
FIT_PROPS = ("points", "rebounds", "threes")
FAVORABLE_GAP = 0.05


def blend_toward(base: float, other: float, strength: float = MODEL_D_STRENGTH) -> float:
    """Prespecified share of the existing positional adjustment. 0 leaves the base unchanged."""
    return base + strength * (other - base)


def _played(row: dict) -> bool:
    try:
        return row.get("min") is not None and float(row.get("min")) > 0
    except (TypeError, ValueError):
        return False


def _safe_div(num: float, den: float) -> float | None:
    if den <= 0:
        return None
    return num / den


def _accumulate(rows: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        if not _played(row):
            continue
        slot = position_bucket(str(row.get("position") or ""))
        if not slot:
            continue
        pace = float(row.get("pace") or 0)
        if pace <= 0:
            continue
        slot_row = buckets.setdefault(slot, {
            "games": 0, "fga": 0.0, "pts": 0.0, "fg3a": 0.0, "fg3m": 0.0,
            "chances": 0.0, "reb": 0.0, "pace": 0.0,
        })
        slot_row["games"] += 1
        slot_row["fga"] += float(row.get("fga") or 0)
        slot_row["pts"] += float(row.get("pts") or 0)
        slot_row["fg3a"] += float(row.get("fg3a") or 0)
        slot_row["fg3m"] += float(row.get("fg3m") or 0)
        chances = row.get("rebound_chances_total")
        if chances is not None:
            slot_row["chances"] += float(chances)
        slot_row["reb"] += float(row.get("reb") or 0)
        slot_row["pace"] += pace
    return buckets


def _rate(bucket: dict, kind: str) -> float | None:
    games = bucket["games"]
    if games <= 0:
        return None
    pace = bucket["pace"] / games
    per_100 = 100.0 / pace if pace > 0 else None
    if per_100 is None:
        return None
    if kind == "fga":
        return bucket["fga"] / games * per_100
    if kind == "ppp":
        return _safe_div(bucket["pts"], bucket["fga"])
    if kind == "fg3a":
        return bucket["fg3a"] / games * per_100
    if kind == "fg3pct":
        return _safe_div(bucket["fg3m"], bucket["fg3a"])
    if kind == "chances":
        return bucket["chances"] / games * per_100
    if kind == "secure":
        return _safe_div(bucket["reb"], bucket["chances"])
    return None


def _shrunk_gap(team_rate: float | None, league_rate: float | None, games: int, slot: str) -> tuple[float, int]:
    if team_rate is None or league_rate is None or league_rate <= 0 or games <= 0:
        return 0.0, 0
    raw = (team_rate - league_rate) / league_rate
    weight = (games / (games + SHRINK_GAMES)) * MAPPING_CONFIDENCE.get(slot, 0.5)
    return raw * weight, games


def matchup_gaps(logs: list[dict], game_day, defense_team_id: str) -> dict[str, dict]:
    """
    Pace-normalized positional gaps from games strictly before ``game_day``.

    Current-season games only. Last year's roster is not this defense.
    Defensive rating and fantasy points allowed are not in these gaps.
    """
    prior = [
        row for row in logs_before(logs, game_day)
        if row.get("season") == "2025-26"
    ]
    against = [
        row for row in prior
        if str(row.get("opponent_team_id") or "") == str(defense_team_id)
    ]
    league_rows = [row for row in prior if row.get("opponent_team_id")]
    team_buckets = _accumulate(against)
    league_buckets = _accumulate(league_rows)
    kinds = ("fga", "ppp", "fg3a", "fg3pct", "chances", "secure")
    out: dict[str, dict] = {}
    for slot in ("PG", "SG", "SF", "PF", "C"):
        team = team_buckets.get(slot) or {"games": 0}
        league = league_buckets.get(slot)
        games = int(team.get("games") or 0)
        slot_gaps = {"sample_games": games, "mapping_confidence": MAPPING_CONFIDENCE[slot]}
        for kind in kinds:
            gap, _n = _shrunk_gap(
                _rate(team, kind) if games else None,
                _rate(league, kind) if league else None,
                games,
                slot,
            )
            slot_gaps[kind] = gap
        out[slot] = slot_gaps
    return out


def exposure_for(player, prop: str) -> tuple[float, float]:
    """Return (volume exposure, efficiency exposure). Missing tracking stays 0."""
    if prop == "points":
        fga = float(player.field_goal_attempts or 0)
        return fga, fga
    if prop == "threes":
        attempts = float(player.three_point_attempts or 0)
        return attempts, attempts
    if prop == "rebounds":
        chances = float(player.rebound_chances or 0)
        return chances, chances
    return 0.0, 0.0


def interaction_features(gaps: dict, player, prop: str) -> dict:
    """
    Separate volume and efficiency channels.

    Assists are omitted: game-level potential assists do not exist, so an
    opponent 'quality of pass' effect cannot be separated from conversion.
    """
    slot = position_bucket(player.position.value if hasattr(player.position, "value") else str(player.position))
    # Players were stored on the five-slot enum. G maps through position_bucket
    # only when the raw string is still broad. The stored position is already PG/SG/SF/PF/C.
    if player.position.value in {"PG", "SG", "SF", "PF", "C"}:
        slot = player.position.value
    slot_gaps = gaps.get(slot) or {}
    volume_name = {"points": "fga", "threes": "fg3a", "rebounds": "chances"}.get(prop)
    efficiency_name = {"points": "ppp", "threes": "fg3pct", "rebounds": "secure"}.get(prop)
    volume_exposure, efficiency_exposure = exposure_for(player, prop)
    volume_gap = float(slot_gaps.get(volume_name) or 0.0) if volume_name else 0.0
    efficiency_gap = float(slot_gaps.get(efficiency_name) or 0.0) if efficiency_name else 0.0
    return {
        "x_volume": volume_gap * volume_exposure,
        "x_efficiency": efficiency_gap * efficiency_exposure,
        "volume_gap": volume_gap,
        "efficiency_gap": efficiency_gap,
        "sample_games": int(slot_gaps.get("sample_games") or 0),
        "mapping_confidence": float(slot_gaps.get("mapping_confidence") or 0.0),
        "usage": float(player.usage_rate or 0.0),
        "minutes": float(player.minutes_per_game or 0.0),
        "identified": prop in FIT_PROPS and volume_exposure > 0,
    }


def _solve(a: float, b: float, d: float, e: float, f: float) -> tuple[float, float]:
    det = a * d - b * b
    if abs(det) < 1e-12:
        if a > 1e-12 and abs(b) < 1e-8:
            return e / a, 0.0
        if d > 1e-12 and abs(b) < 1e-8:
            return 0.0, f / d
        return 0.0, 0.0
    return (e * d - b * f) / det, (a * f - e * b) / det


def fit_ridge(rows: list[dict], lam: float) -> tuple[float, float]:
    """No intercept. A zero feature leaves Model A's probability unchanged."""
    a = d = e = f = b = 0.0
    for row in rows:
        x1 = float(row["x_volume"])
        x2 = float(row["x_efficiency"])
        resid = float(row["outcome"]) - float(row["p_a"])
        a += x1 * x1
        d += x2 * x2
        b += x1 * x2
        e += x1 * resid
        f += x2 * resid
    a += lam
    d += lam
    return _solve(a, b, d, e, f)


def adjusted_probability(p_a: float, x_volume: float, x_efficiency: float, coef: tuple[float, float]) -> float:
    raw = float(p_a) + coef[0] * float(x_volume) + coef[1] * float(x_efficiency)
    return clamp(raw, 0.001, 0.999)


def _brier(rows: list[dict], coef: tuple[float, float]) -> float:
    if not rows:
        return 1.0
    total = 0.0
    for row in rows:
        prob = adjusted_probability(row["p_a"], row["x_volume"], row["x_efficiency"], coef)
        total += (prob - float(row["outcome"])) ** 2
    return total / len(rows)


def _scale(rows: list[dict]) -> tuple[list[dict], dict]:
    """Scale features by this sample's spread. The scale is frozen with the fit."""
    def _sd(key: str) -> float:
        values = [float(row[key]) for row in rows]
        if len(values) < 2:
            return 1.0
        center = sum(values) / len(values)
        var = sum((value - center) ** 2 for value in values) / (len(values) - 1)
        return var ** 0.5 or 1.0

    scale = {"x_volume": _sd("x_volume"), "x_efficiency": _sd("x_efficiency")}
    scaled = []
    for row in rows:
        copy = dict(row)
        copy["x_volume"] = float(row["x_volume"]) / scale["x_volume"]
        copy["x_efficiency"] = float(row["x_efficiency"]) / scale["x_efficiency"]
        scaled.append(copy)
    return scaled, scale


def select_lambda(rows: list[dict]) -> dict:
    """
    Leave-one-date-out on the training window.

    The null model (no interaction) is a candidate. February is not an input.
    """
    dates = sorted({str(row["game_date"]) for row in rows})
    if len(dates) < 3 or len(rows) < 20:
        return {
            "lambda": None,
            "coefficients": (0.0, 0.0),
            "scale": {"x_volume": 1.0, "x_efficiency": 1.0},
            "reason": "Training window is too small to fit an interaction. Coefficients stay at 0.",
            "cv": [],
        }
    cv = []
    for lam in list(LAMBDA_GRID) + [None]:
        held = []
        for day in dates:
            train = [row for row in rows if str(row["game_date"]) != day]
            test = [row for row in rows if str(row["game_date"]) == day]
            if not train or not test:
                continue
            train_s, scale = _scale(train)
            coef = (0.0, 0.0) if lam is None else fit_ridge(train_s, lam)
            test_s = []
            for row in test:
                copy = dict(row)
                copy["x_volume"] = float(row["x_volume"]) / scale["x_volume"]
                copy["x_efficiency"] = float(row["x_efficiency"]) / scale["x_efficiency"]
                test_s.append(copy)
            held.append(_brier(test_s, coef))
        cv.append({
            "lambda": lam,
            "mean_held_out_brier": (sum(held) / len(held)) if held else None,
            "n_dates": len(held),
        })
    ranked = [row for row in cv if row["mean_held_out_brier"] is not None]
    ranked.sort(key=lambda row: row["mean_held_out_brier"])
    chosen = ranked[0] if ranked else {"lambda": None}
    scaled, scale = _scale(rows)
    coef = (0.0, 0.0) if chosen.get("lambda") is None else fit_ridge(scaled, float(chosen["lambda"]))
    return {
        "lambda": chosen.get("lambda"),
        "coefficients": coef,
        "scale": scale,
        "reason": (
            "Null model won January date cross-validation. No interaction is applied."
            if chosen.get("lambda") is None
            else "Lambda and coefficients were frozen from January date cross-validation."
        ),
        "cv": cv,
    }


def apply_scale(x_volume: float, x_efficiency: float, scale: dict) -> tuple[float, float]:
    return (
        float(x_volume) / float(scale.get("x_volume") or 1.0),
        float(x_efficiency) / float(scale.get("x_efficiency") or 1.0),
    )


def matchup_band(volume_gap: float) -> str:
    if volume_gap >= FAVORABLE_GAP:
        return "favorable"
    if volume_gap <= -FAVORABLE_GAP:
        return "unfavorable"
    return "neutral"


def usage_band(usage: float, low: float, high: float) -> str:
    if usage >= high:
        return "high"
    if usage <= low:
        return "low"
    return "middle"
