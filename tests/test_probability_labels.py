"""Board labels say where a probability came from, and pace says when it is held neutral."""

from __future__ import annotations

from types import SimpleNamespace

from app import ui
from domain.entities import TeamDefense
from engine.backtest.variants import neutral_defense
from models.opportunity import NEUTRAL_PACE_SOURCE, pace_label


def _prop(method="market_blend", weight=0.0, model=0.258, consensus=0.376):
    return SimpleNamespace(
        probability_method=method, model_weight=weight,
        raw_model_probability=model, market_probability=consensus,
    )


def test_zero_weight_blend_is_labeled_as_consensus():
    p = _prop(weight=0.0)
    assert ui.probability_label(p) == "Blended"
    assert ui.edge_basis(p) == "vs consensus"
    note = ui.probability_note(p)
    assert "Model weight is 0" in note
    assert "37.6%" in note and "25.8%" in note


def test_weighted_blend_names_both_inputs():
    p = _prop(weight=0.5, model=0.616, consensus=0.234)
    assert ui.edge_basis(p) == "edge"
    note = ui.probability_note(p)
    assert "23.4%" in note and "61.6%" in note and "0.50" in note


def test_legacy_props_keep_model_label_and_no_note():
    p = _prop(method="legacy")
    assert ui.probability_label(p) == "Model"
    assert ui.edge_basis(p) == "edge"
    assert ui.probability_note(p) == ""


def test_missing_consensus_is_stated():
    assert "unavailable" in ui.probability_note(_prop(weight=0.0, consensus=None))


def test_edge_chip_shows_basis():
    assert "+2.5% vs consensus" in ui.edge_chip(0.025, "vs consensus")
    assert "+2.5% edge" in ui.edge_chip(0.025)


def test_meter_uses_given_label():
    html = ui.prob_meter(0.4, 0.35, true_label="Blended")
    assert "Blended <b>40.0%</b>" in html
    assert "Price <b>35.0%</b>" in html


def test_neutral_pace_is_not_called_measured():
    label = pace_label(100.3, False, NEUTRAL_PACE_SOURCE)
    assert "held at the league average" in label
    assert "BALLDONTLIE team pace" not in label


def test_measured_and_fallback_pace_labels_unchanged():
    assert "BALLDONTLIE team pace" in pace_label(98.4, False, "balldontlie")
    assert "fallback" in pace_label(100.3, True)


def test_neutral_defense_carries_the_neutral_source():
    d = neutral_defense(TeamDefense(team_id="9", team_abbr="DET", pace=98.4))
    assert d.pace_source == NEUTRAL_PACE_SOURCE
