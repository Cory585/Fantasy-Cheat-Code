"""Live draft models: an ESPN league's draft as it happens."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.data.sources.espn_draft import (
    ESPN_S2_PATTERN,
    LEAGUE_ID_PATTERN,
    SWID_PATTERN,
    DraftStatus,
    EspnDraftTeam,
)
from app.models.player import Position


class LiveDraftRequest(BaseModel):
    """Start following an ESPN league's draft. Private leagues need both cookies."""

    league_id: str = Field(pattern=LEAGUE_ID_PATTERN, description="The number after leagueId= in the ESPN league URL")
    season: int | None = Field(default=None, ge=2018, le=2100, description="Defaults to the current year")
    espn_s2: str | None = Field(default=None, pattern=ESPN_S2_PATTERN, description="ESPN cookie (private leagues)")
    swid: str | None = Field(default=None, pattern=SWID_PATTERN, description="ESPN cookie (private leagues)")

    @model_validator(mode="after")
    def both_cookies_or_neither(self) -> "LiveDraftRequest":
        if (self.espn_s2 is None) != (self.swid is None):
            raise ValueError("Send both espn_s2 and SWID, or neither for a public league.")
        return self


class LivePick(BaseModel):
    overall: int
    round: int
    round_pick: int
    team_id: int = Field(description="ESPN team id of the team that made the pick")
    espn_player_id: int
    keeper: bool = False
    player_id: str | None = Field(default=None, description="Our player id (None if the player isn't in the pool)")
    name: str | None = None
    position: Position | None = None
    team: str | None = Field(default=None, description="NFL team")


class LiveDraft(BaseModel):
    league_id: str
    season: int
    name: str
    status: DraftStatus
    draft_type: str = Field(description="ESPN draft type, e.g. SNAKE or AUCTION")
    team_count: int
    rounds: int
    teams: list[EspnDraftTeam] = Field(description="Teams in draft order (slot 1 first) when the order is set")
    picks: list[LivePick] = Field(description="Picks made so far, in order")
    updated_at: datetime = Field(description="When ESPN was last read")


class LiveDraftSession(BaseModel):
    session_id: str = Field(description="Use with /draft/live/{session_id} and its /events stream")
    draft: LiveDraft
