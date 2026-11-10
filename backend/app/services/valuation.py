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


# Positions that can fill a FLEX lineup slot.
FLEX_POSITIONS = (Position.RB, Position.WR, Position.TE)


class ValuationService:
    def __init__(self, repo: PlayerRepository):
        self._repo = repo
        self._levels: dict[tuple, dict[Position, float]] = {}

    def replacement_level(self, position: Position, league: LeagueSettings) -> float:
        """Projection of the best player at `position` not expected to start in this league."""
        key = (league.num_teams, tuple(sorted(league.roster_slots.items())))
        if key not in self._levels:
            self._levels[key] = self._replacement_levels(league)
        return self._levels[key][position]

    def _replacement_levels(self, league: LeagueSettings) -> dict[Position, float]:
        """Fill every team's lineup with the best available players, then take the best
        leftover at each position as its replacement level.

        Dedicated slots fill first (e.g. 2 RB x 12 teams = the top 24 RBs). FLEX slots then
        go to the best remaining RB/WR/TE, so a WR3 who starts at flex has value instead of
        sitting below the replacement line with the waiver wire.
        """
        pools = {pos: self._repo.list(pos) for pos in Position}
        taken = {pos: min(league.roster_slots.get(pos.value, 0) * league.num_teams, len(pools[pos])) for pos in Position}

        flex_slots = league.roster_slots.get("FLEX", 0) * league.num_teams
        for _ in range(flex_slots):
            candidates = [pos for pos in FLEX_POSITIONS if taken[pos] < len(pools[pos])]
            if not candidates:
                break
            best = max(candidates, key=lambda pos: pools[pos][taken[pos]].ros_projection)
            taken[best] += 1

        # A pool too small to have anyone left over (e.g. the sample data) gets a 0 baseline.
        return {
            pos: pools[pos][taken[pos]].ros_projection if taken[pos] < len(pools[pos]) else 0.0
            for pos in Position
        }

    def value(self, player: Player, league: LeagueSettings) -> PlayerValuation:
        notes: list[str] = []
        raw = player.ros_projection - self.replacement_level(player.position, league)
        value = max(raw, 0.0)

        if player.injury_status:
            factor = INJURY_DISCOUNT.get(player.injury_status, 1.0)
            value *= factor
            notes.append(f"Injury status {player.injury_status}: value x{factor}")

        return PlayerValuation(player=player, value=round(value, 2), notes=notes)
