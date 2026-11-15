"""Player tags: short labels (slot receiver, high target share, rookie...) for quick lookup."""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.player import Position


class TagCategory(str, Enum):
    ROLE = "role"  # how a player is used: slot receiver, workhorse back, rushing QB
    USAGE = "usage"  # volume and opportunity: target share, red-zone looks, snap share
    TREND = "trend"  # form and outcomes: hot streak, boom or bust, TD-dependent
    STATUS = "status"  # rookie, injury, bye this week, schedule


class TagDefinition(BaseModel):
    key: str
    label: str
    category: TagCategory
    description: str = Field(description="What earns the tag, in plain words")
    positions: list[Position]


class PlayerTag(BaseModel):
    key: str
    detail: str | None = Field(default=None, description="The numbers behind the tag, e.g. '27% target share'")


class TagSummary(TagDefinition):
    count: int = Field(description="Players in the pool with this tag")


class TagIndex(BaseModel):
    season: int | None = Field(description="Season the stat-based tags come from (None without collected data)")
    tags: list[TagSummary]
    players: dict[str, list[PlayerTag]] = Field(description="Player id -> tags, in catalog order")
