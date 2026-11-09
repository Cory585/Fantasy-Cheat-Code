"""Player valuation.

Current approach: value over replacement (VOR). A player's value is their
rest-of-season projection minus the projection of a "replacement-level" player
at the same position (roughly the best player likely on waivers).

TODO:
  - Scoring-format adjustments (PPR vs. standard) once projections are format-aware.
  - Blend in tier data, schedule strength, and injury risk.
  - Swap in the improved projections model when it exists.
"""

from app.data.repository import PlayerRepository
from app.models.player import Player, Position
from app.models.trade import LeagueSettings, PlayerValuation

# Discount applied to injured players' value until a real risk model exists.
INJURY_DISCOUNT = {"Q": 0.9, "D": 0.75, "O": 0.6, "IR": 0.2}


class ValuationService:
    def __init__(self, repo: PlayerRepository):
        self._repo = repo

    def replacement_level(self, position: Position, league: LeagueSettings) -> float:
        """Projection of the first player at `position` not expected to be rostered as a starter."""
        starters = league.roster_slots.get(position.value, 0) * league.num_teams
        pool = self._repo.list(position)
        if starters < len(pool):
            return pool[starters].ros_projection
        # Sample pool is too small to find a real baseline; treat replacement as 0.
        return 0.0

    def value(self, player: Player, league: LeagueSettings) -> PlayerValuation:
        notes: list[str] = []
        raw = player.ros_projection - self.replacement_level(player.position, league)
        value = max(raw, 0.0)

        if player.injury_status:
            factor = INJURY_DISCOUNT.get(player.injury_status, 1.0)
            value *= factor
            notes.append(f"Injury status {player.injury_status}: value x{factor}")

        return PlayerValuation(player=player, value=round(value, 2), notes=notes)
