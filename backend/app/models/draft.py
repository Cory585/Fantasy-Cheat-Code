"""Draft guide models."""

from pydantic import BaseModel, Field

from app.models.player import Position


class DraftPick(BaseModel):
    round: int
    pick_in_round: int = Field(description="1-based position within the round, in draft order")
    overall: int
    label: str = Field(description="Round and pick, e.g. '3.05'")


class DraftTarget(BaseModel):
    """A player likely to be on the board at one of your picks."""

    player_id: str
    name: str
    position: Position
    team: str
    adp: float | None = Field(default=None, description="Average draft position (None if the player has none)")
    projection: float
    rank: int = Field(description="1-based position in the board order (ADP, then projection)")


class PlannedPick(BaseModel):
    pick: DraftPick
    targets: list[DraftTarget] = Field(default_factory=list)


class DraftPlan(BaseModel):
    teams: int
    slot: int = Field(description="Your draft slot (1 = first pick of round 1)")
    rounds: int
    adp_source: str = Field(description="'espn' when ADP was available, else 'projection'")
    picks: list[PlannedPick]
