"""Tier list models."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.player import Position


class TierPlayer(BaseModel):
    player_id: str
    name: str
    position: Position
    team: str
    bye_week: int | None = None
    injury_status: str | None = None
    rank: int = Field(description="1-based rank in the whole list")
    points: float = Field(description="Projected points for the horizon (rest of season, or the week)")
    value: float = Field(description="What's tiered: points, or points over replacement in the flex view, "
                                     "times injury_factor")
    injury_factor: float = Field(1.0, description="Share of rest-of-season points kept for the injury status "
                                                  "(the trade analyzer's discount); 1 in the week view")


class Tier(BaseModel):
    tier: int = Field(description="1 = best")
    players: list[TierPlayer]
    high: float = Field(description="Best value in the tier")
    low: float = Field(description="Worst value in the tier")
    drop_to_next: float | None = Field(
        default=None, description="Gap from this tier's last player to the next tier's first (None for the last tier)"
    )


class TierList(BaseModel):
    position: str = Field(description="QB, RB, WR, TE, K, DST, or FLEX (RB, WR, and TE together)")
    horizon: Literal["ros", "week"]
    week: int | None = Field(default=None, description="The week, when horizon is 'week'")
    teams: int = Field(description="League size, which sets how deep the list goes (and flex replacement levels)")
    metric: Literal["points", "value_over_replacement"]
    tiers: list[Tier]
