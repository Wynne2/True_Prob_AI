"""
Cross-provider field normalisation.

Converts raw provider response dicts into validated Pydantic schemas and
then into typed domain entities.  Every provider routes through here so
downstream code never sees raw API responses.
"""

from __future__ import annotations

import logging
from typing import Any

from domain.constants import POSITION_ALIAS_MAP, PROP_ALIAS_MAP
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import (
    BookName,
    DataSource,
    InjuryStatus,
    PlayerRole,
    Position,
    PropType,
)
from domain.schemas import (
    RawGameSchema,
    RawOddsLineSchema,
    RawPlayerSchema,
    RawTeamDefenseSchema,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position normalisation
# ---------------------------------------------------------------------------

def normalise_position(raw: str) -> Position:
    """Map any provider position string to a Position enum value."""
    clean = raw.strip().upper()
    # Direct match first
    try:
        return Position(clean)
    except ValueError:
        pass
    # Alias map
    alias = POSITION_ALIAS_MAP.get(raw.strip().lower())
    if alias:
        try:
            return Position(alias)
        except ValueError:
            pass
    logger.debug("Unknown position '%s', defaulting to G", raw)
    return Position.G


def normalise_prop_type(raw: str) -> PropType | None:
    """Map any provider market/prop string to a PropType enum value."""
    clean = raw.strip().lower()
    return PROP_ALIAS_MAP.get(clean)


def normalise_injury_status(raw: str) -> InjuryStatus:
    clean = raw.strip().lower().replace(" ", "_")
    try:
        return InjuryStatus(clean)
    except ValueError:
        if "out" in clean:
            return InjuryStatus.OUT
        if "question" in clean:
            return InjuryStatus.QUESTIONABLE
        if "doubt" in clean:
            return InjuryStatus.DOUBTFUL
        if "day" in clean:
            return InjuryStatus.DAY_TO_DAY
        return InjuryStatus.ACTIVE


def normalise_role(raw: str) -> PlayerRole:
    clean = raw.strip().lower()
    try:
        return PlayerRole(clean)
    except ValueError:
        if "start" in clean:
            return PlayerRole.STARTER
        return PlayerRole.BENCH


def normalise_book_name(raw: str) -> BookName:
    clean = raw.strip().lower().replace(" ", "")
    try:
        return BookName(clean)
    except ValueError:
        logger.debug("Unknown book '%s', mapping to SAMPLE", raw)
        return BookName.SAMPLE


# ---------------------------------------------------------------------------
# Entity converters
# ---------------------------------------------------------------------------

def raw_dict_to_game(raw: dict[str, Any], source: DataSource = DataSource.SAMPLE) -> Game | None:
    """Validate and convert a raw dict to a Game entity."""
    try:
        raw["source"] = source.value
        schema = RawGameSchema(**raw)
        return Game(
            game_id=schema.game_id,
            home_team_id=schema.home_team_id,
            home_team_abbr=schema.home_team_abbr,
            away_team_id=schema.away_team_id,
            away_team_abbr=schema.away_team_abbr,
            game_date=schema.game_date,
            tip_off_time=schema.tip_off_utc,
            arena=schema.arena or "",
            city=schema.city or "",
            game_total=schema.game_total,
            home_spread=schema.home_spread,
            home_implied_total=schema.home_implied_total,
            away_implied_total=schema.away_implied_total,
            blowout_risk=schema.blowout_risk,
            is_back_to_back_home=schema.is_back_to_back_home,
            is_back_to_back_away=schema.is_back_to_back_away,
            data_source=source,
        )
    except Exception as exc:
        logger.warning("Failed to normalise game record: %s | raw=%s", exc, raw)
        return None


def raw_dict_to_player(raw: dict[str, Any], source: DataSource = DataSource.SAMPLE) -> Player | None:
    """Validate and convert a raw dict to a Player entity."""
    try:
        raw["source"] = source.value
        schema = RawPlayerSchema(**raw)
        return Player(
            player_id=schema.player_id,
            name=schema.name,
            team_id=schema.team_id,
            team_abbr=schema.team_abbr,
            position=normalise_position(schema.position),
            role=normalise_role(schema.role),
            injury_status=normalise_injury_status(schema.injury_status),
            minutes_per_game=schema.minutes_per_game,
            points_per_game=schema.points_per_game,
            rebounds_per_game=schema.rebounds_per_game,
            assists_per_game=schema.assists_per_game,
            threes_per_game=schema.threes_per_game,
            blocks_per_game=schema.blocks_per_game,
            steals_per_game=schema.steals_per_game,
            turnovers_per_game=schema.turnovers_per_game,
            usage_rate=schema.usage_rate,
            field_goal_attempts=schema.field_goal_attempts,
            free_throw_attempts=schema.free_throw_attempts,
            three_point_attempts=schema.three_point_attempts,
            three_point_pct=schema.three_point_pct,
            touches=schema.touches,
            time_of_possession=schema.time_of_possession,
            rebound_chances=schema.rebound_chances,
            potential_assists=schema.potential_assists,
            home_ppg=schema.home_ppg,
            away_ppg=schema.away_ppg,
            is_starter=schema.is_starter,
            last5_points=schema.last5_points,
            last5_rebounds=schema.last5_rebounds,
            last5_assists=schema.last5_assists,
            last5_minutes=schema.last5_minutes,
            last5_threes=schema.last5_threes,
            last10_points=schema.last10_points,
            last10_rebounds=schema.last10_rebounds,
            last10_assists=schema.last10_assists,
            last10_minutes=schema.last10_minutes,
            data_source=source,
        )
    except Exception as exc:
        logger.warning("Failed to normalise player record: %s | raw=%s", exc, raw)
        return None


def raw_dict_to_team_defense(
    raw: dict[str, Any], source: DataSource = DataSource.SAMPLE
) -> TeamDefense | None:
    """Validate and convert a raw dict to a TeamDefense entity."""
    try:
        raw["source"] = source.value
        schema = RawTeamDefenseSchema(**raw)
        td = TeamDefense(team_id=schema.team_id, team_abbr=schema.team_abbr, data_source=source)
        for field_name in schema.model_fields:
            if field_name in ("team_id", "team_abbr", "source"):
                continue
            setattr(td, field_name, getattr(schema, field_name))
        return td
    except Exception as exc:
        logger.warning("Failed to normalise team defense record: %s | raw=%s", exc, raw)
        return None


def raw_dict_to_odds_line(
    raw: dict[str, Any], source: DataSource = DataSource.SAMPLE
) -> OddsLine | None:
    """Validate and convert a raw dict to an OddsLine entity."""
    try:
        raw["source"] = source.value
        schema = RawOddsLineSchema(**raw)

        prop_type = normalise_prop_type(schema.prop_type)
        if prop_type is None:
            logger.debug("Skipping unrecognised prop type: %s", schema.prop_type)
            return None

        return OddsLine(
            book=normalise_book_name(schema.book),
            player_id=schema.player_id,
            player_name=schema.player_name,
            prop_type=prop_type,
            line=schema.line,
            over_odds=schema.over_odds,
            under_odds=schema.under_odds,
            game_id=schema.game_id,
            team_abbr=schema.team_abbr,
            opponent_abbr=schema.opponent_abbr,
            timestamp=schema.timestamp,
            is_alternate_line=schema.is_alternate_line,
            data_source=source,
        )
    except Exception as exc:
        logger.warning("Failed to normalise odds line: %s | raw=%s", exc, raw)
        return None


# ---------------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------------

def normalise_games(
    raw_list: list[dict[str, Any]], source: DataSource
) -> list[Game]:
    results = []
    for raw in raw_list:
        entity = raw_dict_to_game(raw, source)
        if entity:
            results.append(entity)
    return results


def normalise_players(
    raw_list: list[dict[str, Any]], source: DataSource
) -> list[Player]:
    results = []
    for raw in raw_list:
        entity = raw_dict_to_player(raw, source)
        if entity:
            results.append(entity)
    return results


def normalise_odds_lines(
    raw_list: list[dict[str, Any]], source: DataSource
) -> list[OddsLine]:
    results = []
    for raw in raw_list:
        entity = raw_dict_to_odds_line(raw, source)
        if entity:
            results.append(entity)
    return results
