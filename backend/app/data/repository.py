"""Player data access.

`PlayerRepository` is the seam for real data sources (Sleeper, ESPN, FantasyPros,
a local DB, ...). The in-memory implementation lets the API run end to end today.
"""

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
