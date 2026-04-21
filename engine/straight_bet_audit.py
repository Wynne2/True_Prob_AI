"""
Straight-bet UI audit helpers (favorite American odds band).

The evaluator attaches ``favorite_band_audit`` to legs in the configured band;
this module adds **post-scan** straight-tab filter reasons using the same rules
as ``_qualifying_props_for_display`` (min edge + leg odds band). Parlay-only
filters (e.g. min confidence) are **not** applied to straight bets.
"""

from __future__ import annotations

from typing import Any

from domain.constants import (
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH,
    FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW,
)
from engine.parlay_builder import leg_odds_match_constraints


def build_favorite_band_audit_table(
    all_props: list[Any],
    min_leg_odds: int,
    max_leg_odds: int,
    min_edge: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Flatten ``favorite_band_audit`` payloads and add straight-tab filter columns.

    Returns (rows, summary) where summary counts band props, positive edge, and
    removals by each filter.
    """
    rows: list[dict[str, Any]] = []
    for p in all_props:
        a = getattr(p, "favorite_band_audit", None)
        if not a:
            continue
        reasons: list[str] = []
        if not leg_odds_match_constraints(
            int(p.sportsbook_odds), int(min_leg_odds), int(max_leg_odds)
        ):
            reasons.append(
                f"leg_odds {p.sportsbook_odds} outside UI band [{min_leg_odds}, {max_leg_odds}]"
            )
        if float(p.edge) < float(min_edge):
            reasons.append(
                f"edge {float(p.edge):.4f} < min_edge {float(min_edge):.4f}"
            )
        filtered = bool(reasons)
        row = {
            "player": p.player_name,
            "prop": p.prop_type.value,
            "line": p.line,
            "side": p.side.value.upper(),
            "american": int(p.sportsbook_odds),
            "raw_implied": a.get("raw_implied_probability"),
            "fair_implied": a.get("fair_implied_probability"),
            "raw_projected_mean": a.get("raw_projected_mean"),
            "adjusted_projected_mean": a.get("adjusted_projected_mean"),
            "uncapped_true_prob": a.get("uncapped_true_probability"),
            "step1_tail_pre_shrink": a.get("step1_tail_before_probability_shrink"),
            "final_true_prob": a.get("final_true_probability"),
            "final_edge": a.get("final_edge"),
            "confidence": a.get("confidence_tier"),
            "filtered_out": filtered,
            "filter_reason": "; ".join(reasons) if reasons else "shown by straight-tab filters",
        }
        rows.append(row)

    n_band = len(rows)
    n_pos_edge = sum(1 for r in rows if (r.get("final_edge") or 0) > 0)
    n_pos_pass = sum(
        1
        for r in rows
        if (r.get("final_edge") or 0) > 0 and not r.get("filtered_out")
    )
    n_removed_edge = sum(
        1 for r in rows if (r.get("final_edge") or 0) > 0 and r.get("filtered_out")
    )
    summary = {
        "band_low": FAVORITE_STRAIGHT_BET_AUDIT_BAND_LOW,
        "band_high": FAVORITE_STRAIGHT_BET_AUDIT_BAND_HIGH,
        "props_in_evaluator_band": n_band,
        "positive_edge_in_band": n_pos_edge,
        "positive_edge_passing_straight_filters": n_pos_pass,
        "positive_edge_removed_by_ui_filters": n_removed_edge,
        "ui_min_edge": float(min_edge),
        "ui_leg_odds_range": [int(min_leg_odds), int(max_leg_odds)],
        "straight_tab_uses_parlay_min_confidence": False,
        "notes": (
            "Straight tab = min_edge + leg odds only (see _qualifying_props_for_display). "
            "Parlays also apply min_confidence and combined odds (not used for straight list)."
        ),
    }
    return rows, summary


def _filtered_reason(
    p: Any, min_leg_odds: int, max_leg_odds: int, min_edge: float
) -> tuple[bool, str]:
    reasons: list[str] = []
    if not leg_odds_match_constraints(
        int(p.sportsbook_odds), int(min_leg_odds), int(max_leg_odds)
    ):
        reasons.append(
            f"leg_odds {p.sportsbook_odds} outside UI band [{min_leg_odds}, {max_leg_odds}]"
        )
    if float(p.edge) < float(min_edge):
        reasons.append(
            f"edge {float(p.edge):.4f} < min_edge {float(min_edge):.4f}"
        )
    filtered = bool(reasons)
    return filtered, "; ".join(reasons) if reasons else "shown by straight-tab filters"


def top_uncapped_minus_fair_gap(
    all_props: list[Any],
    min_leg_odds: int,
    max_leg_odds: int,
    min_edge: float,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    Rank legs in the evaluator favorite band by (uncapped_true_prob - fair_implied).

    Each row includes pipeline probabilities and straight-tab filter columns.
    """
    ranked: list[tuple[float, Any, dict[str, Any]]] = []
    for p in all_props:
        a = getattr(p, "favorite_band_audit", None)
        if not a:
            continue
        fair = float(a.get("fair_implied_probability") or 0.0)
        unc = float(a.get("uncapped_true_probability") or 0.0)
        gap = unc - fair
        ranked.append((gap, p, a))
    ranked.sort(key=lambda x: -x[0])
    out: list[dict[str, Any]] = []
    for gap, p, a in ranked[:top_n]:
        filt, reason = _filtered_reason(p, min_leg_odds, max_leg_odds, min_edge)
        out.append(
            {
                "uncapped_minus_fair_gap": round(gap, 5),
                "player": p.player_name,
                "prop": p.prop_type.value,
                "line": p.line,
                "side": p.side.value.upper(),
                "american": int(p.sportsbook_odds),
                "fair_implied": a.get("fair_implied_probability"),
                "uncapped_true_prob": a.get("uncapped_true_probability"),
                "after_shrink_probability": a.get("after_shrink_probability"),
                "true_probability_before_market_calibration": a.get(
                    "true_probability_before_market_calibration"
                ),
                "final_true_prob": a.get("final_true_probability"),
                "final_edge": a.get("final_edge"),
                "filtered_out": filt,
                "filter_reason": reason,
            }
        )
    return out


def pipeline_drop_averages_for_positive_edge_favorites(all_props: list[Any]) -> dict[str, Any]:
    """
    Mean downward probability moves for band legs with **final_edge > 0**.

    Steps (model):
      1. shrinkage: step1_tail - after_shrink
      2. completeness: after_shrink - after_completeness
      3a. structural (not in user 5-list): after_completeness - pre_market (step4/audit/volatile)
      3. market calibration: pre_market - post_market
      4. final gate: post_market - final
      5. UI: no change to evaluated true_prob (0.0); visibility only

    Drops use max(0, earlier - later) so increases are ignored.
    """
    s1_list: list[float] = []
    sh_list: list[float] = []
    co_list: list[float] = []
    pre_list: list[float] = []
    post_list: list[float] = []
    fin_list: list[float] = []
    for p in all_props:
        a = getattr(p, "favorite_band_audit", None)
        if not a:
            continue
        if float(a.get("final_edge") or 0) <= 0:
            continue
        s1 = float(a.get("step1_tail_before_probability_shrink") or 0.0)
        sh = float(a.get("after_shrink_probability") or 0.0)
        co = float(a.get("after_completeness_probability") or 0.0)
        pre = float(a.get("true_probability_before_market_calibration") or 0.0)
        post = float(a.get("true_probability_after_market_calibration") or 0.0)
        fin = float(a.get("final_true_probability") or 0.0)
        s1_list.append(max(0.0, s1 - sh))
        sh_list.append(max(0.0, sh - co))
        co_list.append(max(0.0, co - pre))
        pre_list.append(max(0.0, pre - post))
        post_list.append(max(0.0, post - fin))
    n = len(s1_list)
    if n == 0:
        return {
            "n_positive_edge_in_band": 0,
            "avg_drop_shrinkage": None,
            "avg_drop_completeness": None,
            "avg_drop_structural_pre_market": None,
            "avg_drop_market_calibration": None,
            "avg_drop_final_gate": None,
            "avg_drop_ui_probability": 0.0,
            "largest_among_user_steps_1_to_4": None,
            "note": "No positive-edge legs in band passing straight-tab filters.",
        }

    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    av_shrink = _avg(s1_list)
    av_comp = _avg(sh_list)
    av_struct = _avg(co_list)
    av_mkt = _avg(pre_list)
    av_gate = _avg(post_list)
    user_steps = {
        "1_shrinkage": av_shrink,
        "2_completeness_adjustment": av_comp,
        "3_market_calibration": av_mkt,
        "4_final_gate": av_gate,
    }
    winner = max(user_steps, key=lambda k: user_steps[k])
    return {
        "n_positive_edge_in_band": n,
        "avg_drop_shrinkage": round(av_shrink, 5),
        "avg_drop_completeness": round(av_comp, 5),
        "avg_drop_structural_pre_market": round(av_struct, 5),
        "avg_drop_market_calibration": round(av_mkt, 5),
        "avg_drop_final_gate": round(av_gate, 5),
        "avg_drop_ui_probability": 0.0,
        "largest_among_user_steps_1_to_4": winner,
        "largest_avg_value": round(user_steps[winner], 5),
        "note": (
            "Step **structural_pre_market** is after_completeness → pre-market "
            "(hard clamp, audit-flag shrink, volatile low-line). Compare its average "
            "to steps 1–4 if it dominates."
        ),
    }


def format_favorite_band_summary_markdown(summary: dict[str, Any]) -> str:
    """Short markdown blurb for Streamlit."""
    return (
        f"- **Props evaluated in audit band** [{summary['band_low']}, {summary['band_high']}]: "
        f"**{summary['props_in_evaluator_band']}**\n"
        f"- **Positive edge (final)** in band: **{summary['positive_edge_in_band']}**\n"
        f"- **Positive edge and pass straight-tab filters**: **{summary['positive_edge_passing_straight_filters']}**\n"
        f"- **Positive edge but removed by UI** (min edge / leg band): "
        f"**{summary['positive_edge_removed_by_ui_filters']}**\n"
        f"- **UI min edge**: {summary['ui_min_edge']:.1%} · **Leg odds**: "
        f"{summary['ui_leg_odds_range'][0]} … {summary['ui_leg_odds_range'][1]}\n"
        f"- **Parlay min confidence applies to parlays only** (straight tab does not use it)."
    )
