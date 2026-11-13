"""Team defense breakdown: position groups and unit results, ranked across the league."""

from pydantic import BaseModel, Field


class RankedStat(BaseModel):
    key: str
    label: str
    value: float = Field(description="Per game, or a rate for rate stats")
    rank: int = Field(description="1 = best in the league (for tackles: most)")
    teams: int
    lower_is_better: bool = False


class GroupPlayer(BaseModel):
    gsis_id: str
    name: str
    position: str
    games: int
    stats: dict[str, float] = Field(description="Season totals for the group's stats")


class PositionGroup(BaseModel):
    key: str = Field(description="DL, LB, CB, or S")
    label: str
    rank: int = Field(description="1 = most productive group of its kind in the league")
    teams: int
    score: float = Field(description="Weighted average z-score of the group's per-game stats (0 = league average)")
    stats: list[RankedStat]
    players: list[GroupPlayer] = Field(description="The group's most productive players, best first")


class UnitResult(BaseModel):
    key: str = Field(description="pass_rush, coverage, run_defense, or scoring")
    label: str
    stats: list[RankedStat]


class FantasyAllowed(BaseModel):
    position: str
    points_per_game: float = Field(description="PPR fantasy points allowed per game to the position")
    rank: int = Field(description="1 = fewest allowed (the toughest matchup)")
    teams: int


class UpcomingGame(BaseModel):
    week: int
    opponent: str
    home: bool
    projection: float | None = Field(default=None, description="DST model projection, when the model is trained")
    rank: int | None = Field(default=None, description="Projection rank among defenses playing that week")
    teams: int = 0
    factors: list[tuple[str, float]] = Field(default_factory=list,
                                             description="(reason, points vs an average matchup), biggest first")


class TeamSummary(BaseModel):
    team: str
    name: str | None = None
    logo_url: str | None = None
    color: str | None = None
    games: int
    overall_rank: int = Field(description="Rank of the average of the four position-group scores")
    group_ranks: dict[str, int]
    points_allowed_pg: float
    points_allowed_rank: int
    dst_ppg: float = Field(description="Standard DST fantasy points per game")
    dst_rank: int


class DefenseLeague(BaseModel):
    season: int
    through_week: int
    teams: list[TeamSummary] = Field(description="Every team, best overall first")


class DefenseBreakdown(BaseModel):
    season: int
    through_week: int
    summary: TeamSummary
    groups: list[PositionGroup]
    units: list[UnitResult]
    fantasy_allowed: list[FantasyAllowed]
    upcoming: list[UpcomingGame]
