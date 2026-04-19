"""Unit tests for TeamDefense assembly (no live API required)."""

from __future__ import annotations

from data.builders.team_defense_builder import _norm_pos, _row_by_position


def test_norm_pos_accepts_standard_labels():
    assert _norm_pos("PG") == "PG"
    assert _norm_pos("pf") == "PF"
    assert _norm_pos("") is None


def test_row_by_position_buckets():
    rows = [
        {
            "opponent_position": "PG",
            "pts_allowed": 24.0,
            "reb_allowed": 5.0,
            "ast_allowed": 8.0,
            "fg3m_allowed": 3.0,
            "fantasy_allowed": 40.0,
        },
        {
            "opponent_position": "C",
            "pts_allowed": 22.0,
            "reb_allowed": 12.0,
            "ast_allowed": 3.0,
            "fg3m_allowed": 1.5,
            "fantasy_allowed": 38.0,
        },
    ]
    m = _row_by_position(rows)
    assert m["PG"]["pts_allowed"] == 24.0
    assert m["C"]["reb_allowed"] == 12.0
