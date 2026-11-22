"""Tiers: groups of players at a position who are roughly interchangeable.

Breaks go where projected points drop sharply. Players are sorted by value and split into
contiguous groups by optimal 1-D clustering: dynamic programming over the sorted values that
minimizes the spread within each tier (sum of squared distances to the tier's mean) plus a
fixed cost per tier. The cost per tier sets how big a drop has to be to start a new tier.
It's scaled to the average gap between neighboring players, so it means the same thing for
QBs scoring 300 points a season as for kickers scoring 8 a week:

  * In a smooth run with gaps about the average, a tier grows to a target size before
    splitting it pays off: a sixth of the list, between `MIN_TARGET_SIZE` and
    `MAX_TARGET_SIZE` players. Long lists get 2-8 player tiers; a short one still splits
    at its real gaps instead of landing in one tier.
  * A single drop several times the average gap starts a new tier on its own.

(The average rather than the median: the bottom of a list is crowded, so a median gap is
tiny and splits the spread-out top into a string of one- and two-player tiers.)

Deterministic, no extra libraries, and O(n^2) for n players (n is at most a few hundred).
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from app.models.player import Player, Position
from app.models.tiers import Tier, TierList, TierPlayer
from app.models.trade import LeagueSettings
from app.services.valuation import FLEX_POSITIONS, INJURY_DISCOUNT, ValuationService, fill_lineups

T = TypeVar("T")

# In a smooth run of players, about how many go in one tier before it's split.
TARGET_TIERS = 6
MIN_TARGET_SIZE = 4
MAX_TARGET_SIZE = 8

# How many players to tier per team in the league, by position (12 teams: 30 QBs, 60 RBs...).
DEPTH_PER_TEAM = {Position.QB: 2.5, Position.RB: 5, Position.WR: 6, Position.TE: 2.5, Position.K: 1.5,
                  Position.DST: 1.5}
FLEX_DEPTH_PER_TEAM = 10


def tier_breaks(values: Sequence[float]) -> list[int]:
    """Start index of each tier for values sorted from best to worst ([0] for one tier)."""
    n = len(values)
    if n <= 1:
        return [0] * n
    if any(values[i] < values[i + 1] for i in range(n - 1)):
        raise ValueError("values must be sorted from best to worst")
    gap = (values[0] - values[-1]) / (n - 1)
    if gap <= 0:
        return [0]  # everyone is tied
    # Splitting a run of m evenly spaced values (gap g) in half lowers the spread by about
    # m^3 g^2 / 16, so this cost per tier lets smooth tiers grow to about `size` players.
    size = min(max(n / TARGET_TIERS, MIN_TARGET_SIZE), MAX_TARGET_SIZE)
    cost_per_tier = size**3 / 16 * gap * gap

    # Prefix sums, so the spread of any run values[i:j] is O(1).
    s = [0.0] * (n + 1)
    s2 = [0.0] * (n + 1)
    for i, v in enumerate(values):
        s[i + 1] = s[i] + v
        s2[i + 1] = s2[i] + v * v

    def spread(i: int, j: int) -> float:
        total = s[j] - s[i]
        return max(s2[j] - s2[i] - total * total / (j - i), 0.0)

    # best[j]: lowest cost of tiering values[:j]; start[j]: where its last tier starts.
    best = [0.0] + [math.inf] * n
    start = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j):
            cost = best[i] + spread(i, j) + cost_per_tier
            if cost < best[j] - 1e-9 * cost_per_tier:  # ties keep the earlier (longer) tier
                best[j], start[j] = cost, i
    breaks = []
    j = n
    while j > 0:
        breaks.append(start[j])
        j = start[j]
    return breaks[::-1]


def group_into_tiers(items: Sequence[T], value: Callable[[T], float]) -> list[list[T]]:
    """Items (sorted best first by `value`) split into tiers."""
    breaks = tier_breaks([value(item) for item in items])
    ends = breaks[1:] + [len(items)]
    return [list(items[a:b]) for a, b in zip(breaks, ends)]


def depth(position: Position | None, teams: int) -> int:
    """How many players to tier at a position (None = flex) for a league of `teams`."""
    per_team = FLEX_DEPTH_PER_TEAM if position is None else DEPTH_PER_TEAM[position]
    return math.ceil(per_team * teams)


@dataclass
class _Scored:
    player: Player
    points: float  # projection for the horizon
    value: float  # what's tiered: points, or points over replacement for flex
    injury_factor: float = 1.0


class TierService:
    def __init__(self, players: list[Player], valuation: ValuationService):
        self._players = players
        self._valuation = valuation

    def _points(self, player: Player, week: int | None,
                week_points: Callable[[Player], float | None] | None) -> float | None:
        if week is None:
            return self._valuation.projection(player)
        if week_points is not None:
            return week_points(player)
        return player.week_projection(week)

    def tiers(self, position: Position | None, teams: int, week: int | None = None,
              max_players: int | None = None,
              week_points: Callable[[Player], float | None] | None = None) -> TierList:
        """Tiers at `position` (None = flex: RB, WR, and TE by points over replacement),
        for the rest of the season, or for one week if `week` is given.

        `week_points` scores a player for that week (the API passes start/sit's projection,
        which counts injuries, byes, and form); without it, the providers' raw projection."""
        positions = FLEX_POSITIONS if position is None else (position,)
        scored = []
        for p in self._players:
            if p.position not in positions:
                continue
            points = self._points(p, week, week_points)
            # Rest of season, an injured player keeps only part of their value, as in the trade
            # analyzer: otherwise a player out for weeks tiers with healthy starters.
            factor = INJURY_DISCOUNT.get(p.injury_status or "", 1.0) if week is None else 1.0
            if points is not None and points > 0:
                scored.append(_Scored(p, points, points * factor, factor))
        if position is None:
            # Replacement levels from the full projections, as ValuationService sets them, and the
            # discount applied to the value over replacement, as the trade analyzer applies it.
            league = LeagueSettings(num_teams=teams)
            pools = {pos: sorted((s.points for s in scored if s.player.position == pos), reverse=True)
                     for pos in Position}
            levels, _ = fill_lineups(pools, league)
            for s in scored:
                s.value = (s.points - levels[s.player.position]) * s.injury_factor

        scored.sort(key=lambda s: (-s.value, s.player.name, s.player.id))
        scored = scored[:max_players or depth(position, teams)]
        groups = group_into_tiers(scored, lambda s: s.value)

        tiers = []
        rank = 0
        for number, group in enumerate(groups, start=1):
            players = []
            for s in group:
                rank += 1
                players.append(TierPlayer(
                    player_id=s.player.id, name=s.player.name, position=s.player.position, team=s.player.team,
                    bye_week=s.player.bye_week, injury_status=s.player.injury_status, rank=rank,
                    points=round(s.points, 1), value=round(s.value, 1), injury_factor=s.injury_factor,
                ))
            tiers.append(Tier(tier=number, players=players, high=players[0].value, low=players[-1].value))
        for tier, following in zip(tiers, tiers[1:]):
            tier.drop_to_next = round(tier.low - following.high, 1)
        return TierList(
            position="FLEX" if position is None else position.value,
            horizon="ros" if week is None else "week",
            week=week, teams=teams, metric="points" if position is not None else "value_over_replacement",
            tiers=tiers,
        )
