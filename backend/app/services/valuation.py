"""Player valuation.

Value over replacement (VOR): a player's value is their rest-of-season projection
minus the projection of a "replacement-level" player at the same position (roughly
the best player likely on waivers), discounted for injury status.

When the collected database is available, the projection is the provider projection
blended with schedule-adjusted recent form (see `app.services.outlook`).

TODO:
  - Blend in tier data and a real injury-risk model.
"""

from dataclasses import dataclass

from app.data.repository import PlayerRepository
from app.models.player import Player, PlayerOutlook, Position
from app.models.trade import LeagueSettings, PlayerValuation
from app.services.outlook import schedule_label

# Discount applied to injured players' value until a real risk model exists.
INJURY_DISCOUNT = {"Q": 0.9, "D": 0.75, "O": 0.6, "IR": 0.2}


# Positions that can fill a FLEX lineup slot.
FLEX_POSITIONS = (Position.RB, Position.WR, Position.TE)
# Providers this far apart (relative to their average) make a projection shaky; this close, solid.
PROJECTIONS_DISAGREE = 0.15
PROJECTIONS_AGREE = 0.05
TOP_AT_POSITION = 3
STREAMABLE = (Position.K, Position.DST)
PROVIDER_NAMES = {"sleeper": "Sleeper", "espn": "ESPN"}


def fill_lineups(pools: dict[Position, list[float]], league: LeagueSettings) -> tuple[dict[Position, float], dict[Position, int]]:
    """Fill every team's lineup with the best available players, then take the best leftover
    at each position as its replacement level. `pools` holds each position's projections,
    best first. Returns the replacement levels and how many players start at each position.

    Dedicated slots fill first (e.g. 2 RB x 12 teams = the top 24 RBs). FLEX slots then go
    to the best remaining RB/WR/TE, so a WR3 who starts at flex has value instead of sitting
    below the replacement line with the waiver wire.
    """
    pools = {pos: pools.get(pos, []) for pos in Position}
    taken = {pos: min(league.roster_slots.get(pos.value, 0) * league.num_teams, len(pools[pos])) for pos in Position}

    flex_slots = league.roster_slots.get("FLEX", 0) * league.num_teams
    for _ in range(flex_slots):
        candidates = [pos for pos in FLEX_POSITIONS if taken[pos] < len(pools[pos])]
        if not candidates:
            break
        best = max(candidates, key=lambda pos: pools[pos][taken[pos]])
        taken[best] += 1

    # A pool too small to have anyone left over (e.g. the sample data) gets a 0 baseline.
    levels = {pos: pools[pos][taken[pos]] if taken[pos] < len(pools[pos]) else 0.0 for pos in Position}
    return levels, taken


@dataclass
class _LeagueTable:
    levels: dict[Position, float]  # replacement-level projection
    starters: dict[Position, int]  # players at the position who start league-wide (flex included)
    ranks: dict[str, int]  # player id -> rank at the position by projection


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
        return self._table(league).levels[position]

    def _table(self, league: LeagueSettings) -> _LeagueTable:
        key = (league.num_teams, tuple(sorted(league.roster_slots.items())))
        if key not in self._levels:
            self._levels[key] = self._replacement_levels(league)
        return self._levels[key]

    def _replacement_levels(self, league: LeagueSettings) -> _LeagueTable:
        pools = {
            pos: sorted(self._repo.list(pos), key=self.projection, reverse=True) for pos in Position
        }
        levels, taken = fill_lineups({pos: [self.projection(p) for p in pool] for pos, pool in pools.items()}, league)
        ranks = {p.id: i + 1 for pos in Position for i, p in enumerate(pools[pos])}
        return _LeagueTable(levels, taken, ranks)

    def value(self, player: Player, league: LeagueSettings) -> PlayerValuation:
        outlook = self._outlooks.get(player.id)
        projection = self.projection(player)
        notes = outlook_notes(player, outlook)
        value = max(projection - self.replacement_level(player.position, league), 0.0)

        factor = 1.0
        if player.injury_status:
            factor = INJURY_DISCOUNT.get(player.injury_status, 1.0)
            value *= factor
            notes.append(f"Injury status {player.injury_status}: value x{factor}")

        table = self._table(league)
        rank = table.ranks.get(player.id)
        starter = rank is not None and rank <= table.starters[player.position]
        tier = f"{player.position.value}{(rank - 1) // max(league.num_teams, 1) + 1}" if rank else None
        strengths, concerns = player_traits(
            player, outlook, league, rank=rank, tier=tier, starter=starter, value=value,
            injury_factor=factor, replacement=table.levels[player.position])
        return PlayerValuation(
            player=player, value=round(value, 2), projection=round(projection, 2), outlook=outlook, notes=notes,
            position_rank=rank, tier=tier, starter=starter if rank else None,
            strengths=strengths, concerns=concerns,
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


def player_traits(player: Player, outlook: PlayerOutlook | None, league: LeagueSettings, *, rank: int | None,
                  tier: str | None, starter: bool, value: float, injury_factor: float,
                  replacement: float) -> tuple[list[str], list[str]]:
    """(strengths, concerns): plain-language reasons a player is worth having, or worth less."""
    strengths, concerns = [], []
    pos = player.position.value
    teams = league.num_teams

    if rank is not None:
        where = f"{ordinal(rank)} at {pos} by rest-of-season projection"
        if rank <= TOP_AT_POSITION:
            strengths.append(f"Top-{TOP_AT_POSITION} {pos}: {where}")
        elif starter:
            strengths.append(f"{tier} who starts in a {teams}-team league ({where})")
        else:
            concerns.append(f"Below the starting line in a {teams}-team league ({where}): bench depth")

    if value > 0 and replacement > 0:
        strengths.append(f"{value:.0f} points better than a replacement-level {pos} the rest of the season")
    elif value <= 0 and replacement > 0:
        concerns.append(f"No value over replacement: a similar {pos} is likely on waivers")

    if outlook is not None:
        label = schedule_label(outlook.sos_rank, outlook.sos_teams)
        if label == "easy":
            strengths.append(f"{_rank_phrase(outlook.sos_rank, 'easiest')} remaining schedule for {pos}s")
        elif label == "hard":
            hardest = outlook.sos_teams - outlook.sos_rank + 1
            concerns.append(f"{_rank_phrase(hardest, 'hardest')} remaining schedule for {pos}s")
        if outlook.form_projection is not None and outlook.recent_ppg is not None and outlook.provider_projection:
            trend = outlook.form_projection - outlook.provider_projection
            if abs(trend) >= 0.1 * outlook.provider_projection:
                direction = "above" if trend > 0 else "below"
                text = (f"Recent form ({outlook.recent_ppg:.1f} pts/game), adjusted for the remaining schedule, "
                        f"points {direction} projections")
                (strengths if trend > 0 else concerns).append(text)

    if player.injury_status:
        concerns.append(f"Listed {player.injury_status}: trade value cut to {injury_factor:.0%}")

    sources = {k: v for k, v in player.projection_sources.items() if v}
    if len(sources) >= 2:
        low, high = min(sources.values()), max(sources.values())
        spread = (high - low) / (sum(sources.values()) / len(sources))
        named = " vs ".join(f"{PROVIDER_NAMES.get(k, k)} {v:.0f}" for k, v in sorted(sources.items()))
        if spread >= PROJECTIONS_DISAGREE:
            concerns.append(f"Projections disagree ({named}), so the outlook is less certain")
        elif spread <= PROJECTIONS_AGREE:
            strengths.append(f"Projections agree ({named})")

    if player.position in STREAMABLE:
        group = "Kickers" if player.position == Position.K else "Defenses"
        concerns.append(f"{group} swing week to week and are easy to stream")
    return strengths, concerns


def _rank_phrase(rank: int, superlative: str) -> str:
    """1 -> 'Hardest', 3 -> '3rd hardest'."""
    return superlative.capitalize() if rank == 1 else f"{ordinal(rank)} {superlative}"
