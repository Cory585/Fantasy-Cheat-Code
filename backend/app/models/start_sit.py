"""Start/sit request and response models."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.player import Player, Position

MIN_COMPARE = 2
MAX_COMPARE = 8


class LineupSlot(str, Enum):
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    FLEX = "FLEX"  # RB, WR, or TE
    SUPERFLEX = "SUPERFLEX"  # QB, RB, WR, or TE
    K = "K"
    DST = "DST"


SLOT_POSITIONS: dict[LineupSlot, frozenset[Position]] = {
    LineupSlot.QB: frozenset({Position.QB}),
    LineupSlot.RB: frozenset({Position.RB}),
    LineupSlot.WR: frozenset({Position.WR}),
    LineupSlot.TE: frozenset({Position.TE}),
    LineupSlot.FLEX: frozenset({Position.RB, Position.WR, Position.TE}),
    LineupSlot.SUPERFLEX: frozenset({Position.QB, Position.RB, Position.WR, Position.TE}),
    LineupSlot.K: frozenset({Position.K}),
    LineupSlot.DST: frozenset({Position.DST}),
}


class GameStatus(str, Enum):
    UPCOMING = "upcoming"
    PLAYED = "played"  # the game is final; `projection` is the actual score when known
    BYE = "bye"


class Effect(str, Enum):
    PLUS = "plus"
    MINUS = "minus"
    NEUTRAL = "neutral"


class Reason(BaseModel):
    kind: str = Field(description="projection, matchup, vegas, form, model, injury, bye, played, or estimate")
    text: str
    effect: Effect = Effect.NEUTRAL


class Matchup(BaseModel):
    opponent: str
    home: bool
    factor: float | None = Field(default=None, description="Points the defense allows vs average (1.10 = 10% more)")
    rank: int | None = Field(default=None, description="1 = easiest matchup at the position")
    teams: int = 0
    label: str | None = Field(default=None, description="easy, average, or hard")
    implied_total: float | None = None
    implied_rank: int | None = Field(default=None, description="1 = most implied points this week")
    favored_by: float | None = None


class PlayerWeek(BaseModel):
    player: Player
    week: int
    status: GameStatus
    projection: float = Field(description="Expected points this week, after injury risk")
    if_active: float = Field(description="Expected points if the player plays")
    provider_projection: float | None = Field(default=None, description="Mean of provider projections for the week")
    sources: dict[str, float] = Field(default_factory=dict)
    floor: float = Field(description="About a 10th-percentile outcome if the player plays")
    ceiling: float = Field(description="About a 90th-percentile outcome if the player plays")
    play_probability: float
    matchup: Matchup | None = None
    recent_ppg: float | None = None
    recent_games: int = 0
    actual_points: float | None = None
    start_probability: float | None = Field(
        default=None, description="Share of simulated weeks this player was among the best choices"
    )
    recommended: bool = False
    reasons: list[Reason] = Field(default_factory=list)


class Confidence(str, Enum):
    CONFIDENT = "confident"
    LEAN = "lean"
    TOSS_UP = "toss_up"


class Recommendation(BaseModel):
    start: list[str]
    sit: list[str]
    probability: float = Field(
        description="How often the closest starter outscores the best benched player in simulated weeks"
    )
    confidence: Confidence
    summary: str


class StartSitAnalysis(BaseModel):
    season: int | None
    week: int
    slot: LineupSlot
    starts: int
    players: list[PlayerWeek] = Field(description="Best to worst by expected points")
    recommendation: Recommendation
    warnings: list[str] = Field(default_factory=list)


# ---------- Lineups (a whole roster) ----------

MAX_ROSTER = 40
MAX_SLOT_COUNT = 5
DEFAULT_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}


class LineupRequest(BaseModel):
    roster_ids: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]] = Field(
        min_length=1, max_length=MAX_ROSTER
    )
    slots: dict[LineupSlot, Annotated[int, Field(ge=0, le=MAX_SLOT_COUNT)]] = Field(
        default_factory=lambda: {LineupSlot(k): v for k, v in DEFAULT_LINEUP.items()},
        description="Starting lineup, e.g. {\"QB\": 1, \"RB\": 2, \"WR\": 2, \"TE\": 1, \"FLEX\": 1}",
    )
    week: int | None = Field(default=None, ge=1, le=18)


class LineupSpot(BaseModel):
    slot: LineupSlot
    starter: PlayerWeek | None = Field(description="None when the roster has nobody left who fits")
    alternative: PlayerWeek | None = Field(
        default=None, description="The best benched player who could fill this spot"
    )
    probability: float | None = Field(
        default=None, description="How often the starter outscores the alternative in simulated weeks"
    )
    confidence: Confidence | None = None


class LineupAnalysis(BaseModel):
    season: int | None
    week: int
    lineup: list[LineupSpot]
    bench: list[PlayerWeek] = Field(description="Best to worst by expected points")
    projected_total: float
    close_calls: list[int] = Field(
        default_factory=list, description="Indexes into `lineup` of the closest calls, closest first"
    )
    missing_ids: list[str] = Field(
        default_factory=list, description="Roster ids that aren't in the player pool (skipped)"
    )
    warnings: list[str] = Field(default_factory=list)
