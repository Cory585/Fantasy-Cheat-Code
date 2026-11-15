"""A league on someone's profile: their team in it (name, lineup spots, roster)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.start_sit import MAX_ROSTER, MAX_SLOT_COUNT, LineupSlot

MAX_LEAGUES = 10
MAX_NAME = 40
LEAGUE_ID_PATTERN = r"^[A-Za-z0-9_-]{8,32}$"
# The client makes this (32 random bytes, base64url) and keeps it in the browser. The server
# only stores its SHA-256, so a leaked database doesn't reveal anyone's key.
PROFILE_KEY_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"

DEFAULT_SLOTS: dict[str, int] = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DST": 1}

PlayerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
SlotCount = Annotated[int, Field(ge=0, le=MAX_SLOT_COUNT)]


class LeagueInput(BaseModel):
    """What a client sends to make or save a league. Missing slots keep their defaults."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    slots: dict[LineupSlot, SlotCount] = Field(default_factory=dict)
    roster: list[PlayerId] = Field(default_factory=list, max_length=MAX_ROSTER)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("A league needs a name.")
        return value

    @field_validator("roster")
    @classmethod
    def _unique_roster(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    def full_slots(self) -> dict[str, int]:
        return {**DEFAULT_SLOTS, **{slot.value: count for slot, count in self.slots.items()}}


class League(BaseModel):
    id: str
    name: str
    slots: dict[str, int]
    roster: list[str]
    created: str = Field(description="ISO 8601, UTC")
    updated: str = Field(description="ISO 8601, UTC")


class LeagueList(BaseModel):
    leagues: list[League]
    max_leagues: int = MAX_LEAGUES
