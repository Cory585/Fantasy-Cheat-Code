"""Trade request/response models."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.models.player import Player, PlayerOutlook


class ScoringFormat(str, Enum):
    STANDARD = "standard"
    HALF_PPR = "half_ppr"
    PPR = "ppr"


# Input limits, far above real use, so a single request can't ask for unbounded work.
MAX_TEAMS = 32
MAX_SLOT_STARTERS = 10
MAX_PLAYERS_PER_SIDE = 40
MAX_ROSTER_SIZE = 100
MAX_TEAM_NAME = 50
ROSTER_SLOT_NAMES = ("QB", "RB", "WR", "TE", "FLEX", "K", "DST")

PlayerId = Annotated[str, Field(min_length=1, max_length=64)]


class LeagueSettings(BaseModel):
    scoring: ScoringFormat = ScoringFormat.PPR
    num_teams: int = Field(default=12, ge=1, le=MAX_TEAMS)
    # Starting lineup slots, e.g. {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
    roster_slots: dict[str, Annotated[int, Field(ge=0, le=MAX_SLOT_STARTERS)]] = Field(
        default_factory=lambda: {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
    )

    @field_validator("roster_slots")
    @classmethod
    def known_slots(cls, slots: dict[str, int]) -> dict[str, int]:
        # Unsupported slots (e.g. SUPERFLEX) would otherwise be silently ignored.
        unknown = sorted(set(slots) - set(ROSTER_SLOT_NAMES))
        if unknown:
            raise ValueError(f"unsupported roster slot(s): {', '.join(unknown)}; use {', '.join(ROSTER_SLOT_NAMES)}")
        return slots


class TradeSide(BaseModel):
    """One team's side of the trade: the players they are giving away."""

    team_name: str | None = Field(default=None, max_length=MAX_TEAM_NAME)
    player_ids: list[PlayerId] = Field(min_length=1, max_length=MAX_PLAYERS_PER_SIDE)
    # Optional full roster, used for roster-fit / lineup-impact analysis.
    roster_ids: list[PlayerId] = Field(default_factory=list, max_length=MAX_ROSTER_SIZE)


class TradeRequest(BaseModel):
    side_a: TradeSide
    side_b: TradeSide
    league: LeagueSettings = Field(default_factory=LeagueSettings)


class Verdict(str, Enum):
    FAVORS_A = "favors_a"
    FAVORS_B = "favors_b"
    FAIR = "fair"


class Effect(str, Enum):
    PLUS = "plus"
    MINUS = "minus"
    NEUTRAL = "neutral"


class Reason(BaseModel):
    """One reason a side comes out ahead or behind."""

    kind: str = Field(description="value, best_player, roster_spots, starters, positions, schedule, form, "
                                  "injury, or uncertainty")
    text: str
    effect: Effect


class PlayerValuation(BaseModel):
    player: Player
    value: float
    # Rest-of-season projection the value is based on (provider projection blended with
    # schedule-adjusted form when the outlook is available).
    projection: float
    outlook: PlayerOutlook | None = None
    notes: list[str] = Field(default_factory=list)
    position_rank: int | None = Field(default=None, description="Rank at the position by projection (1 = best)")
    tier: str | None = Field(default=None, description="e.g. 'RB1': the top N at the position in an N-team league")
    starter: bool | None = Field(default=None, description="Projects to start in a league with these lineup slots")
    strengths: list[str] = Field(default_factory=list, description="What makes the player worth having")
    concerns: list[str] = Field(default_factory=list, description="What could make the player worth less")


class SideAnalysis(BaseModel):
    team_name: str | None
    gives: list[PlayerValuation]
    total_value_given: float
    total_value_received: float
    net_value: float
    # Change in projected weekly starting-lineup points (requires roster_ids).
    lineup_impact: float | None = None
    reasons: list[Reason] = Field(default_factory=list,
                                  description="Why this side comes out ahead or behind, most important first")


class BalanceSuggestion(BaseModel):
    """Players the winning side could add to even out a lopsided trade."""

    side: str = Field(description="'a' or 'b': the side that should add a player")
    value_gap: float
    players: list[PlayerValuation]


class TradeAnalysis(BaseModel):
    verdict: Verdict
    value_difference_pct: float
    side_a: SideAnalysis
    side_b: SideAnalysis
    summary: str
    warnings: list[str] = Field(default_factory=list)
    balance: BalanceSuggestion | None = None
