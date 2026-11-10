"""Player valuation.

Value over replacement (VOR): a player's value is their rest-of-season projection
minus the projection of a "replacement-level" player at the same position (roughly
the best player likely on waivers), discounted for injury status.

When the collected database is available, the projection is the provider projection
blended with schedule-adjusted recent form (see `app.services.outlook`).

TODO:
  - Blend in tier data and a real injury-risk model.
"""

from app.data.repository import PlayerRepository
from app.models.player import Player, PlayerOutlook, Position
from app.models.trade import LeagueSettings, PlayerValuation
from app.services.outlook import schedule_label

# Discount applied to injured players' value until a real risk model exists.
INJURY_DISCOUNT = {"Q": 0.9, "D": 0.75, "O": 0.6, "IR": 0.2}


# Positions that can fill a FLEX lineup slot.
FLEX_POSITIONS = (Position.RB, Position.WR, Position.TE)


class ValuationService:
    def __init__(self, repo: PlayerRepository, outlooks: dict[str, PlayerOutlook] | None = None):
        self._repo = repo
        self._outlooks = outlooks or {}
        self._levels: dict[tuple, dict[Position, float]] = {}

    def projection(self, player: Player) -> float:
        """Projection used for value: schedule- and form-adjusted when an outlook exists."""
        outlook = self._outlooks.get(player.id)
        return outlook.adjusted_projection if outlook else player.ros_projection

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
        pools = {
            pos: sorted(self._repo.list(pos), key=self.projection, reverse=True) for pos in Position
        }
        taken = {pos: min(league.roster_slots.get(pos.value, 0) * league.num_teams, len(pools[pos])) for pos in Position}

        flex_slots = league.roster_slots.get("FLEX", 0) * league.num_teams
        for _ in range(flex_slots):
            candidates = [pos for pos in FLEX_POSITIONS if taken[pos] < len(pools[pos])]
            if not candidates:
                break
            best = max(candidates, key=lambda pos: self.projection(pools[pos][taken[pos]]))
            taken[best] += 1

        # A pool too small to have anyone left over (e.g. the sample data) gets a 0 baseline.
        return {
            pos: self.projection(pools[pos][taken[pos]]) if taken[pos] < len(pools[pos]) else 0.0
            for pos in Position
        }

    def value(self, player: Player, league: LeagueSettings) -> PlayerValuation:
        outlook = self._outlooks.get(player.id)
        projection = self.projection(player)
        notes = outlook_notes(player, outlook)
        value = max(projection - self.replacement_level(player.position, league), 0.0)

        if player.injury_status:
            factor = INJURY_DISCOUNT.get(player.injury_status, 1.0)
            value *= factor
            notes.append(f"Injury status {player.injury_status}: value x{factor}")

        return PlayerValuation(
            player=player, value=round(value, 2), projection=round(projection, 2), outlook=outlook, notes=notes
        )


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def outlook_notes(player: Player, outlook: PlayerOutlook | None) -> list[str]:
    if outlook is None:
        return []
    notes = []
    label = schedule_label(outlook.sos_rank, outlook.sos_teams)
    position = f"{player.position.value}s"
    if label == "easy":
        notes.append(f"{_rank_phrase(outlook.sos_rank, 'easiest')} remaining schedule for {position}")
    elif label == "hard":
        hardest = outlook.sos_teams - outlook.sos_rank + 1
        notes.append(f"{_rank_phrase(hardest, 'hardest')} remaining schedule for {position}")
    if outlook.form_projection is not None and outlook.recent_ppg is not None:
        trend = outlook.form_projection - outlook.provider_projection
        if abs(trend) >= 0.1 * outlook.provider_projection:
            direction = "above" if trend > 0 else "below"
            notes.append(
                f"Recent form ({outlook.recent_ppg:.1f} pts/game over this schedule) is running {direction} projections"
            )
    return notes


def _rank_phrase(rank: int, superlative: str) -> str:
    """1 -> 'Hardest', 3 -> '3rd hardest'."""
    return superlative.capitalize() if rank == 1 else f"{ordinal(rank)} {superlative}"
