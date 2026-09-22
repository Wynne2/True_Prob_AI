"""Explicit provider selection for demo, replay, and BALLDONTLIE live modes."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config import get_credentials, get_settings
from domain.entities import Game, OddsLine, Player, TeamDefense
from domain.enums import PropType
from providers.balldontlie_client import BallDontLieRequestError
from providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Routes every live data request to exactly one external provider."""

    def __init__(self, provider: Optional[BaseProvider], *, mode: str) -> None:
        self._provider = provider
        self.mode = mode
        self.last_failure: Optional[BallDontLieRequestError] = None

    @classmethod
    def build(cls) -> "ProviderRegistry":
        settings = get_settings()
        if settings.app_mode not in {"demo", "live", "replay"}:
            raise ValueError("APP_MODE must be demo, live, or replay")
        if settings.app_mode == "demo":
            from providers.sample_provider import SampleProvider
            return cls(SampleProvider(), mode="demo")
        if settings.app_mode == "replay":
            return cls(None, mode="replay")

        credentials = get_credentials()
        if not credentials.balldontlie_key:
            return cls(None, mode="live")
        from providers.balldontlie_provider import BallDontLieProvider
        return cls(BallDontLieProvider(credentials.balldontlie_key), mode="live")

    def _call(self, method: str, *args, **kwargs):
        if self._provider is None:
            if self.mode == "live":
                self.last_failure = BallDontLieRequestError(
                    "missing_credential",
                    "BALLDONTLIE_API_KEY is required in APP_MODE=live.",
                )
            return [] if method.startswith("get_") else None
        try:
            self.last_failure = None
            return getattr(self._provider, method)(*args, **kwargs)
        except BallDontLieRequestError as exc:
            self.last_failure = exc
            logger.warning("Provider request failed [%s]: %s", exc.code, exc)
            return [] if method.startswith("get_") else None
        except NotImplementedError:
            return [] if method.startswith("get_") else None

    def get_games_for_date(self, game_date: date) -> list[Game]:
        return self._call("get_games_for_date", game_date) or []

    def get_players_for_game(
        self,
        game_id: str,
        candidate_player_ids: Optional[set[str]] = None,
    ) -> list[Player]:
        return self._call("get_players_for_game", game_id, candidate_player_ids) or []

    def get_player_context(self, player_id: str) -> Optional[Player]:
        return self._call("get_player_context", player_id)

    def get_player_recent_form(self, player_id: str, n: int = 10) -> list[dict]:
        return self._call("get_player_recent_form", player_id, n) or []

    def get_team_defense(self, team_id: str) -> Optional[TeamDefense]:
        return self._call("get_team_defense", team_id)

    def get_player_props(self, game_date: date) -> list[OddsLine]:
        return self._call("get_player_props", game_date) or []

    def get_injuries(self, game_date: Optional[date] = None) -> list[dict]:
        return self._call("get_injuries", game_date) or []

    def get_final_player_totals(self, game_id: str) -> dict[str, dict[str, float]]:
        return self._call("get_final_player_totals", game_id) or {}

    @property
    def active_providers(self) -> list[str]:
        return [self._provider.source_name.value] if self._provider else []

    def status(self) -> dict[str, str]:
        if self.last_failure:
            return {"mode": self.mode, "state": "error", "reason": self.last_failure.code}
        if self.mode == "live" and self._provider is None:
            return {"mode": "live", "state": "blocked", "reason": "missing_credential"}
        if self.mode == "replay":
            return {"mode": "replay", "state": "offline", "reason": "network_disabled"}
        return {"mode": self.mode, "state": "ready", "reason": ""}

    def summary(self) -> str:
        status = self.status()
        provider = self.active_providers[0] if self.active_providers else "none"
        detail = f" ({status['reason']})" if status["reason"] else ""
        return f"Provider Registry\n  Mode: {status['mode']}\n  Provider: {provider}\n  State: {status['state']}{detail}"
