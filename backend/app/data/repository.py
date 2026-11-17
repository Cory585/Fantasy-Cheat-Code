"""Player data access.

`PlayerRepository` is the seam for real data sources (Sleeper, ESPN, FantasyPros,
a local DB, ...). The in-memory implementation lets the API run end to end today.
"""

import json
from pathlib import Path
from typing import Protocol

from app.models.player import Player, Position


class PlayerRepository(Protocol):
    def get(self, player_id: str) -> Player | None: ...

    def get_many(self, player_ids: list[str]) -> list[Player]: ...

    def list(self, position: Position | None = None) -> list[Player]: ...


class InMemoryPlayerRepository:
    def __init__(self, players: list[Player]):
        self._players = {p.id: p for p in players}

    def get(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def get_many(self, player_ids: list[str]) -> list[Player]:
        return [p for pid in player_ids if (p := self._players.get(pid)) is not None]

    def list(self, position: Position | None = None) -> list[Player]:
        players = self._players.values()
        if position is not None:
            players = [p for p in players if p.position == position]
        return sorted(players, key=lambda p: p.ros_projection, reverse=True)


def load_cache_metadata(path: Path) -> dict:
    """Season, weeks, and scoring the cache was built for (everything except the players)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if k != "players"}


def load_player_cache(path: Path) -> list[Player] | None:
    """Players from the JSON cache written by `app.data.sync`, or None if there is no cache.

    Raises ValueError for a cache with no players, so the API keeps serving what it had
    instead of showing empty pages."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, list) or not players:
        raise ValueError(f"Player cache {path} has no players")
    return [Player.model_validate(p) for p in players]
