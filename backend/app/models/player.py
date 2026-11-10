"""Player domain models."""

from enum import Enum

from pydantic import BaseModel, Field


class Position(str, Enum):
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DST = "DST"


class Player(BaseModel):
    id: str
    name: str
    position: Position
    team: str = Field(description="NFL team abbreviation, e.g. 'KC'")
    bye_week: int | None = None
    # Rest-of-season projected fantasy points (scoring-format specific).
    ros_projection: float = 0.0
    injury_status: str | None = None  # e.g. "Q", "O", "IR"
    # External IDs, used to join data across providers.
    sleeper_id: str | None = None
    espn_id: int | None = None
    # Per-provider rest-of-season projections, e.g. {"sleeper": 150.2, "espn": 161.0}.
    # `ros_projection` is the consensus (mean) of these when real data is loaded.
    projection_sources: dict[str, float] = Field(default_factory=dict)


class PlayerOutlook(BaseModel):
    """Remaining schedule and recent form, from the collected database."""

    provider_projection: float = Field(description="Rest-of-season points from Sleeper/ESPN")
    adjusted_projection: float = Field(description="Provider projection blended with schedule-adjusted form")
    games_remaining: int
    remaining_opponents: list[str] = Field(default_factory=list)
    sos_factor: float | None = Field(
        default=None, description="Opponents' points allowed vs league average (1.10 = 10% easier)"
    )
    sos_rank: int | None = Field(default=None, description="1 = easiest remaining schedule at the position")
    sos_teams: int = 0
    recent_ppg: float | None = None
    recent_games: int = 0
    form_projection: float | None = Field(
        default=None, description="Recent points per game x remaining games x SOS factor"
    )
