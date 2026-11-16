"""Draft guide: snake-draft pick order and players likely available at each of your picks.

Targets are the players ranked around each pick on the board (ADP first, projection as a
fallback). Each player carries their tier at their position (by projection, from the full
pool, so tiers don't shift as players are drafted), and each pick flags positions whose best
remaining tier is likely gone, or down to one player, by your next pick. Roster needs come later.
"""

from app.models.draft import DraftPick, DraftPlan, DraftTarget, PlannedPick, TierAlert
from app.models.player import Player, Position
from app.services.tiers import depth, group_into_tiers


FLOOR_WINDOW = 1.0  # picks below ESPN's maximum ADP that count as "undrafted"
FLOOR_MIN_PLAYERS = 25  # how many players must cluster there before it's treated as a floor


def pick_label(round_: int, pick_in_round: int) -> str:
    return f"{round_}.{pick_in_round:02d}"


def snake_picks(teams: int, slot: int, rounds: int) -> list[DraftPick]:
    """Your picks in a snake draft: slot order in odd rounds, reversed in even rounds."""
    if teams < 1 or rounds < 1:
        raise ValueError("teams and rounds must be at least 1")
    if not 1 <= slot <= teams:
        raise ValueError(f"slot must be between 1 and {teams}")
    picks = []
    for round_ in range(1, rounds + 1):
        pick_in_round = slot if round_ % 2 else teams + 1 - slot
        overall = (round_ - 1) * teams + pick_in_round
        picks.append(DraftPick(round=round_, pick_in_round=pick_in_round, overall=overall,
                               label=pick_label(round_, pick_in_round)))
    return picks


# A tier is "running dry" when this many of its players or fewer will be left at your next pick.
RUNNING_DRY = 1


class DraftPlanner:
    def __init__(self, players: list[Player], adp: dict[int, float] | None = None):
        """`adp` maps ESPN player id to average draft position."""
        self._adp = adp or {}
        self._board = self._rank(players)
        self._tiers: dict[int, dict[str, int]] = {}

    def tiers(self, teams: int) -> dict[str, int]:
        """Player id -> tier at their position, by projection, for a league of `teams`."""
        if teams not in self._tiers:
            tiers: dict[str, int] = {}
            for position in Position:
                pool = sorted((p for p, _ in self._board if p.position == position and p.ros_projection > 0),
                              key=lambda p: (-p.ros_projection, p.name, p.id))[:depth(position, teams)]
                for number, group in enumerate(group_into_tiers(pool, lambda p: p.ros_projection), start=1):
                    tiers.update((p.id, number) for p in group)
            self._tiers[teams] = tiers
        return self._tiers[teams]

    def _player_adp(self, player: Player, floor: float | None) -> float | None:
        adp = self._adp.get(player.espn_id) if player.espn_id is not None else None
        if adp is not None and floor is not None and adp >= floor:
            return None  # parked at ESPN's "undrafted" value, which says nothing about order
        return adp

    def _undrafted_floor(self) -> float | None:
        """ESPN gives undrafted players an ADP just above the last real pick (about 170), so
        hundreds of players share it. If many players cluster within a pick of the maximum,
        everything from there down is treated as having no ADP."""
        if not self._adp:
            return None
        top = max(self._adp.values())
        clustered = sum(1 for adp in self._adp.values() if adp >= top - FLOOR_WINDOW)
        return top - FLOOR_WINDOW if clustered >= FLOOR_MIN_PLAYERS else None

    def _rank(self, players: list[Player]) -> list[tuple[Player, float | None]]:
        """Players in ADP order. Players without a usable ADP are slotted in where their
        projection ranks among everyone, so a high-projection player with a missing ESPN
        mapping isn't buried behind hundreds of drafted players."""
        floor = self._undrafted_floor()
        with_adp = [(p, self._player_adp(p, floor)) for p in players]
        drafted = sorted((t for t in with_adp if t[1] is not None), key=lambda t: (t[1], -t[0].ros_projection))
        if not drafted:
            return sorted(with_adp, key=lambda t: -t[0].ros_projection)

        by_projection = sorted(with_adp, key=lambda t: -t[0].ros_projection)
        projection_rank = {id(t[0]): i for i, t in enumerate(by_projection)}

        def sort_key(t):
            player, adp = t
            if adp is not None:
                return (adp, 0, -player.ros_projection)
            # Stand-in ADP: the ADP of the drafted player at the same projection rank.
            i = projection_rank[id(player)]
            stand_in = drafted[i][1] if i < len(drafted) else float("inf")
            return (stand_in, 1, -player.ros_projection)

        return sorted(with_adp, key=sort_key)

    @property
    def adp_source(self) -> str:
        return "espn" if any(adp is not None for _, adp in self._board) else "projection"

    def _target(self, board: list[tuple[Player, float | None]], index: int,
                tiers: dict[str, int] | None = None) -> DraftTarget:
        p, adp = board[index]
        return DraftTarget(player_id=p.id, name=p.name, position=p.position, team=p.team,
                           adp=adp, projection=p.ros_projection, rank=index + 1,
                           tier=(tiers or {}).get(p.id))

    @staticmethod
    def _tier_alerts(board: list[tuple[Player, float | None]], start: int, next_start: int,
                     tiers: dict[str, int]) -> list[TierAlert]:
        """Positions whose best tier left at this pick (board[start:]) is likely gone, or down
        to RUNNING_DRY players, by your next pick (board[next_start:]), assuming you take
        board[start] and other teams take the players in between."""
        best: dict[Position, int] = {}
        for p, _ in board[start:]:
            tier = tiers.get(p.id)
            if tier is not None and tier < best.get(p.position, tier + 1):
                best[p.position] = tier
        alerts = []
        for position, tier in best.items():
            in_tier = [i for i in range(start, len(board))
                       if board[i][0].position == position and tiers.get(board[i][0].id) == tier]
            left = len(in_tier)
            left_at_next = sum(1 for i in in_tier if i >= next_start)
            if left_at_next <= RUNNING_DRY and left_at_next < left:
                alerts.append(TierAlert(position=position, tier=tier, left=left, left_at_next=left_at_next))
        order = list(Position)
        alerts.sort(key=lambda a: (a.left_at_next, a.tier, order.index(a.position)))
        return alerts

    def plan(self, teams: int, slot: int, rounds: int, targets_per_pick: int = 5,
             drafted: frozenset[str] = frozenset(), picks_made: int = 0) -> DraftPlan:
        """Your remaining picks and who's likely there at each.

        For a draft in progress, `drafted` holds the ids of players already taken and
        `picks_made` how many picks are in (it can exceed `len(drafted)` when a drafted player
        isn't in the player pool). Those players come off the board, your past picks are left
        out, and the rest of the board fills the picks still to come."""
        board = [entry for entry in self._board if entry[0].id not in drafted] if drafted else self._board
        made = max(picks_made, len(self._board) - len(board))
        tiers = self.tiers(teams)
        mine = snake_picks(teams, slot, rounds)
        picks = []
        for i, pick in enumerate(mine):
            if pick.overall <= made:
                continue
            start = pick.overall - 1 - made  # players ranked ahead of this pick are assumed gone
            end = min(start + targets_per_pick, len(board))
            targets = [self._target(board, j, tiers) for j in range(start, end)]

            # Players other teams take between this pick and your next one, assuming you
            # take the top-ranked player here.
            next_overall = mine[i + 1].overall if i + 1 < len(mine) else None
            mix: dict = {}
            if next_overall:
                for p, _ in board[start + 1:next_overall - 1 - made]:
                    mix[p.position] = mix.get(p.position, 0) + 1

            picks.append(PlannedPick(
                pick=pick,
                targets=targets,
                picks_until_next=next_overall - pick.overall - 1 if next_overall else None,
                position_mix=mix,
                tier_alerts=self._tier_alerts(board, start, next_overall - 1 - made, tiers) if next_overall else [],
            ))
        remaining = max(teams * rounds - made, 0)
        mock = [self._target(board, j, tiers) for j in range(min(remaining, len(board)))]
        return DraftPlan(teams=teams, slot=slot, rounds=rounds, adp_source=self.adp_source,
                         picks_made=made, picks=picks, board=mock)
