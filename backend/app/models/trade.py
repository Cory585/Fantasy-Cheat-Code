"""Trade request/response models."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.player import Player


class ScoringFormat(str, Enum):
    STANDARD = "standard"
    HALF_PPR = "half_ppr"
    PPR = "ppr"


class LeagueSettings(BaseModel):
    scoring: ScoringFormat = ScoringFormat.PPR
    num_teams: int = Field(default=12, ge=1)
    # Starting lineup slots, e.g. {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    roster_slots: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=lambda: {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    )


class TradeSide(BaseModel):
    """One team's side of the trade: the players they are giving away."""

    team_name: str | None = None
    player_ids: list[str] = Field(min_length=1)
    # Optional full roster, used for roster-fit / lineup-impact analysis.
    roster_ids: list[str] = Field(default_factory=list)


class TradeRequest(BaseModel):
    side_a: TradeSide
    side_b: TradeSide
    league: LeagueSettings = Field(default_factory=LeagueSettings)


class Verdict(str, Enum):
    FAVORS_A = "favors_a"
    FAVORS_B = "favors_b"
    FAIR = "fair"


class PlayerValuation(BaseModel):
    player: Player
    value: float
    notes: list[str] = Field(default_factory=list)


class SideAnalysis(BaseModel):
    team_name: str | None
    gives: list[PlayerValuation]
    total_value_given: float
    total_value_received: float
    net_value: float
    # Change in projected weekly starting-lineup points (requires roster_ids).
    lineup_impact: float | None = None


class TradeAnalysis(BaseModel):
    verdict: Verdict
    value_difference_pct: float
    side_a: SideAnalysis
    side_b: SideAnalysis
    summary: str
    warnings: list[str] = Field(default_factory=list)
