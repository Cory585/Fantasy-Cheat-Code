"""Draft guide: snake-draft pick order and players likely available at each of your picks.

This is a skeleton. Targets are simply the players ranked around each pick on the board
(ADP first, projection as a fallback). Roster needs, tiers, and positional runs come later.
"""

from app.models.draft import DraftPick, DraftPlan, DraftTarget, PlannedPick
from app.models.player import Player


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


class DraftPlanner:
    def __init__(self, players: list[Player], adp: dict[int, float] | None = None):
        """`adp` maps ESPN player id to average draft position."""
        self._adp = adp or {}
        self._board = self._rank(players)

    def _player_adp(self, player: Player) -> float | None:
        return self._adp.get(player.espn_id) if player.espn_id is not None else None

    def _rank(self, players: list[Player]) -> list[tuple[Player, float | None]]:
        """Players with ADP in ADP order, then everyone else by projection."""
        with_adp = [(p, self._player_adp(p)) for p in players]
        drafted = sorted((t for t in with_adp if t[1] is not None), key=lambda t: (t[1], -t[0].ros_projection))
        rest = sorted((t for t in with_adp if t[1] is None), key=lambda t: -t[0].ros_projection)
        return drafted + rest

    @property
    def adp_source(self) -> str:
        return "espn" if any(adp is not None for _, adp in self._board) else "projection"

    def plan(self, teams: int, slot: int, rounds: int, targets_per_pick: int = 5) -> DraftPlan:
        picks = []
        for pick in snake_picks(teams, slot, rounds):
            start = pick.overall - 1  # players ranked ahead of this pick are assumed gone
            window = self._board[start:start + targets_per_pick]
            targets = [
                DraftTarget(player_id=p.id, name=p.name, position=p.position, team=p.team,
                            adp=adp, projection=p.ros_projection, rank=start + i + 1)
                for i, (p, adp) in enumerate(window)
            ]
            picks.append(PlannedPick(pick=pick, targets=targets))
        return DraftPlan(teams=teams, slot=slot, rounds=rounds, adp_source=self.adp_source, picks=picks)
