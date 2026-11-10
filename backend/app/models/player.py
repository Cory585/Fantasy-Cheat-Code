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
