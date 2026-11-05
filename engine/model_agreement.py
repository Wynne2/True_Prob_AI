"""
Does the model itself back a prop's edge?

With a market-anchored blend, a prop can show a positive edge even when the
statistical model thinks the bet is worse than its price: at model weight 0 the
true probability is the consensus market, so the "edge" is only this venue
pricing differently from the others. Example: a model projecting 11.7 rebounds
gives the under 7.5 a tiny chance, yet the under at +398 shows +2.9% edge
because one exchange priced it above consensus.

The check compares the model's own chance for this side with the venue's
implied chance. When the model's chance is lower, the prop is flagged, its
confidence drops one tier, and the AI Advisor leaves it out by default. The
board still shows it.
"""

from __future__ import annotations

from typing import Optional

MODEL_DISAGREES_FLAG = "model_disagrees_with_price"


def own_model_probability(prop) -> Optional[float]:
    """The model's own chance for this side (production tail or candidate), before market anchoring."""
    raw = float(getattr(prop, "raw_model_probability", 0.0) or 0.0)
    return raw if raw > 0 else None


def model_agrees(prop) -> bool:
    """False only when the model's own chance is below the venue's implied chance."""
    own = own_model_probability(prop)
    return own is None or own >= float(prop.implied_probability)


def flag_model_disagreement(props: list) -> dict:
    """
    Set ``model_agrees`` on every prop. Positive-edge props the model does not
    back get a calibration warning and one confidence tier less. Safe to call twice.
    """
    from engine.final_calibration_gate import downgrade_confidence_for_flags

    checked = disagree = 0
    for prop in props:
        agrees = model_agrees(prop)
        prop.model_agrees = agrees
        if prop.edge <= 0:
            continue
        checked += 1
        if agrees:
            continue
        disagree += 1
        warnings = list(getattr(prop, "calibration_warnings", None) or [])
        if any(w.startswith(MODEL_DISAGREES_FLAG) for w in warnings):
            continue
        own = own_model_probability(prop)
        warnings.append(f"{MODEL_DISAGREES_FLAG}: model {own:.1%} vs price {float(prop.implied_probability):.1%}")
        prop.calibration_warnings = warnings
        prop.confidence = downgrade_confidence_for_flags(prop.confidence, prop.edge)
    return {"positive_edge": checked, "model_disagrees": disagree}
