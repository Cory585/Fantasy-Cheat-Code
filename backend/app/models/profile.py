"""Player profile models: bio, NFL and college stats, combine results."""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.player import Player


class StatColumn(BaseModel):
    label: str = Field(description="Short label, e.g. 'YDS'")
    name: str = Field(description="Full name, e.g. 'Rushing Yards'")


class StatSeason(BaseModel):
    year: int
    team: str | None = None
    values: list[float | None]


class StatCategory(BaseModel):
    name: str
    label: str = Field(description="e.g. 'Rushing'")
    columns: list[StatColumn]
    seasons: list[StatSeason]
    totals: list[float | None] = Field(default_factory=list)


class Bio(BaseModel):
    height: str | None = None
    weight: str | None = None
    age: int | None = None
    birth_place: str | None = None
    experience: str | None = None
    college: str | None = None
    draft: str | None = None
    jersey: str | None = None
    status: str | None = None


class CombineDrill(BaseModel):
    key: str
    label: str
    value: float
    unit: str
    lower_is_better: bool
    percentile: int | None = Field(
        default=None, description="Share of combine participants at the position this result beats (0-100)"
    )
    compared_with: int = Field(0, description="Participants at the position with a result for this drill")


class Combine(BaseModel):
    season: int
    school: str | None = None
    position: str | None = None
    height_in: float | None = None
    weight_lb: float | None = None
    drills: list[CombineDrill] = Field(default_factory=list)


class SourceStatus(BaseModel):
    status: Literal["fresh", "cached", "stale", "not_found", "unavailable", "not_applicable"]
    code: str | None = Field(default=None, description="Why data is missing, when it is (e.g. 'upstream_busy')")
    fetched_at: float | None = Field(default=None, description="Unix time the ESPN data was fetched")
    retry_after: int | None = Field(default=None, description="Seconds until ESPN will be tried again")


class PlayerProfile(BaseModel):
    player: Player
    headshot_url: str | None = None
    bio: Bio
    nfl_stats: list[StatCategory] = Field(default_factory=list)
    college_stats: list[StatCategory] = Field(default_factory=list)
    combine: Combine | None = None
    espn: SourceStatus
