"""
Deterministic pick and parlay table for the AI Advisor.

Every number here comes from ``odds/parlay_math.py`` and the existing parlay
builder (correlation blocking + Gaussian-copula joint probability). The LLM
only receives these numbers; it never produces them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from domain.entities import Parlay, PropProbability
from engine.bankroll_engine import fractional_kelly
from engine.parlay_builder import ParlayConstraints, build_parlays
from odds.normalizer import american_to_decimal, decimal_to_american
from odds.parlay_math import (
    parlay_combined_decimal,
    parlay_combined_edge,
    parlay_combined_implied_probability,
    parlay_expected_value,
    parlay_payout,
    parlay_profit,
)

logger = logging.getLogger(__name__)


@dataclass
class PickRow:
    """One ranked single-leg candidate with straight-bet math at the chosen stake."""
    id: str
    rank: int
    prop: PropProbability
    decimal_odds: float
    stake: float
    total_return: float
    profit: float
    expected_value: float
    kelly_fraction: float = 0.0

    def kelly_stake(self, bankroll: float | None) -> float | None:
        return round(bankroll * self.kelly_fraction, 2) if bankroll else None

    @property
    def venue(self) -> str:
        return self.prop.best_book.value


@dataclass
class ParlayRow:
    """One correlation-checked 2- or 3-leg combination with payout math at the chosen stake."""
    id: str
    leg_ids: list[str]
    combined_decimal_odds: float
    combined_american_odds: int
    implied_probability: float
    true_probability: float
    edge: float
    stake: float
    total_return: float
    profit: float
    expected_value: float
    correlation_risk_score: float
    same_game: bool
    venues: list[str] = field(default_factory=list)
    kelly_fraction: float = 0.0

    def kelly_stake(self, bankroll: float | None) -> float | None:
        return round(bankroll * self.kelly_fraction, 2) if bankroll else None

    @property
    def num_legs(self) -> int:
        return len(self.leg_ids)

    @property
    def single_venue(self) -> bool:
        return len(set(self.venues)) == 1


def leg_identity(obj) -> tuple:
    """Identity tuple shared by PropProbability and ParlayLeg."""
    return (str(obj.player_id), obj.prop_type.value, float(obj.line), obj.side.value, int(obj.sportsbook_odds))


def build_pick_rows(picks: list[PropProbability], stake: float) -> list[PickRow]:
    """Assign stable IDs P1..Pn (in ranked order) and compute straight-bet math."""
    rows: list[PickRow] = []
    for i, prop in enumerate(picks, start=1):
        dec = american_to_decimal(prop.sportsbook_odds)
        rows.append(PickRow(
            id=f"P{i}",
            rank=i,
            prop=prop,
            decimal_odds=dec,
            stake=stake,
            total_return=parlay_payout(stake, dec),
            profit=parlay_profit(stake, dec),
            expected_value=parlay_expected_value(stake, dec, prop.true_probability),
            kelly_fraction=fractional_kelly(prop.true_probability, dec),
        ))
    return rows


def _parlay_to_row(parlay: Parlay, ids_by_identity: dict[tuple, str], stake: float) -> ParlayRow | None:
    leg_ids: list[str] = []
    for leg in parlay.legs:
        pid = ids_by_identity.get(leg_identity(leg))
        if pid is None:
            logger.warning("Parlay leg %s not found among advisor picks; skipping parlay", leg.player_name)
            return None
        leg_ids.append(pid)

    leg_ids.sort(key=lambda x: int(x[1:]))
    odds = [leg.sportsbook_odds for leg in parlay.legs]
    dec = parlay_combined_decimal(odds)
    implied = parlay_combined_implied_probability([leg.implied_probability for leg in parlay.legs])
    true_p = parlay.combined_true_probability  # Gaussian-copula joint probability from the builder
    return ParlayRow(
        id="",
        leg_ids=leg_ids,
        combined_decimal_odds=dec,
        combined_american_odds=decimal_to_american(dec),
        implied_probability=implied,
        true_probability=true_p,
        edge=parlay_combined_edge(true_p, implied),
        stake=stake,
        total_return=parlay_payout(stake, dec),
        profit=parlay_profit(stake, dec),
        expected_value=parlay_expected_value(stake, dec, true_p),
        correlation_risk_score=parlay.correlation_risk_score,
        same_game=len({leg.game_id for leg in parlay.legs}) < len(parlay.legs),
        venues=[leg.sportsbook.value for leg in parlay.legs],
        kelly_fraction=fractional_kelly(true_p, dec),
    )


def build_parlay_rows(
    pick_rows: list[PickRow],
    *,
    stake: float,
    min_odds: int,
    max_odds: int,
    min_edge: float,
) -> list[ParlayRow]:
    """
    Build every non-blocked 2- and 3-leg combination of the advisor picks.

    Rows are sorted by EV ↓, edge ↓, fewer legs, then leg IDs and labelled PL1..PLn.
    """
    if len(pick_rows) < 2:
        return []

    constraints = ParlayConstraints(
        min_edge=min_edge,
        min_legs=2,
        max_legs=3,
        min_leg_odds=min_odds,
        max_leg_odds=max_odds,
        max_results=1000,
    )
    parlays = build_parlays([r.prop for r in pick_rows], constraints)

    ids_by_identity = {leg_identity(r.prop): r.id for r in pick_rows}
    rows = [row for p in parlays if (row := _parlay_to_row(p, ids_by_identity, stake)) is not None]

    rows.sort(key=lambda r: (
        -round(r.expected_value, 9),
        -round(r.edge, 9),
        r.num_legs,
        [int(x[1:]) for x in r.leg_ids],
    ))
    for i, row in enumerate(rows, start=1):
        row.id = f"PL{i}"
    return rows


def best_parlay(rows: list[ParlayRow], num_legs: int) -> ParlayRow | None:
    """Highest-EV parlay with exactly *num_legs* legs (rows are pre-sorted by EV)."""
    for row in rows:
        if row.num_legs == num_legs:
            return row
    return None
