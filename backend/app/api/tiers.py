"""Tier lists: players at a position grouped where projected points drop sharply."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_start_sit_advisor, get_tier_service
from app.api.errors import ApiError
from app.models.player import Player, Position
from app.models.tiers import TierList
from app.services.start_sit import StartSitAdvisor, StartSitError
from app.services.tiers import TierService

router = APIRouter(prefix="/tiers", tags=["tiers"])

TierPosition = Literal["QB", "RB", "WR", "TE", "K", "DST", "FLEX"]


@router.get("", response_model=TierList)
def tiers(
    position: TierPosition = Query(..., description="A position, or FLEX for RB, WR, and TE ranked together"),
    horizon: Literal["ros", "week"] = Query("ros", description="Rest of season, or one week"),
    week: int | None = Query(None, ge=1, le=18, description="With horizon=week. Default: the current week"),
    teams: int = Query(12, ge=4, le=20, description="League size: sets how deep the list goes"),
    depth: int | None = Query(None, ge=1, le=300, description="Players to tier (default: scaled to league size)"),
    service: TierService = Depends(get_tier_service),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> TierList:
    """Players at a position in tiers: players in the same tier are roughly interchangeable,
    and a new tier starts where projected points drop sharply. FLEX ranks RB, WR, and TE
    together by points over replacement for a league of `teams`.

    The week view ranks by start/sit's projection for the week, so injuries, byes, recent form,
    and games already played count."""
    if horizon == "ros" and week is not None:
        raise ApiError(422, "invalid_week", "Pass week only with horizon=week.")
    week_points = None
    if horizon == "week":
        try:
            week = advisor.check_week(week)
        except StartSitError as error:
            raise ApiError(422, error.code, str(error)) from None
        checked = week

        # Not the providers' raw projection, which ignores the injury report: a player who is
        # out or on IR scores 0 and drops off instead of topping the list (#134).
        def week_points(player: Player) -> float:
            return advisor.evaluate(player, checked).projection

    return service.tiers(None if position == "FLEX" else Position(position), teams, week=week, max_players=depth,
                         week_points=week_points)
