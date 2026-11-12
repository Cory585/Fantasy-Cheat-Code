from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query

from app.api.dependencies import get_player_repository, get_start_sit_advisor
from app.api.errors import ApiError
from app.api.players import PLAYER_ID_PATTERN
from app.data.repository import PlayerRepository
from app.models.start_sit import (
    MAX_COMPARE,
    MIN_COMPARE,
    LineupAnalysis,
    LineupRequest,
    LineupSlot,
    StartSitAnalysis,
)
from app.services.start_sit import StartSitAdvisor, StartSitError

router = APIRouter(prefix="/start-sit", tags=["start-sit"])


@router.get("", response_model=StartSitAnalysis)
def start_sit(
    player: Annotated[
        list[Annotated[str, Query(pattern=PLAYER_ID_PATTERN)]],
        Query(min_length=MIN_COMPARE, max_length=MAX_COMPARE, description="Player ids competing for one spot"),
    ],
    week: int | None = Query(None, ge=1, le=18, description="Default: the current week"),
    slot: LineupSlot | None = Query(None, description="Default: the narrowest slot that fits every player"),
    starts: int = Query(1, ge=1, le=MAX_COMPARE - 1, description="How many of the players you can start"),
    repo: PlayerRepository = Depends(get_player_repository),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> StartSitAnalysis:
    """Who to start this week among players competing for the same lineup spot, and why.

    Pass `player` once per player, e.g. `/start-sit?player=4046&player=6794&week=11`.
    """
    players = []
    for player_id in player:
        found = repo.get(player_id)
        if found is None:
            raise ApiError(404, "player_not_found", f"Player '{player_id}' not found")
        players.append(found)
    try:
        return advisor.compare(players, week=week, slot=slot, starts=starts)
    except StartSitError as error:
        raise ApiError(422, error.code, str(error)) from None


@router.post("/lineup", response_model=LineupAnalysis)
def best_lineup(
    request: LineupRequest = Body(...),
    repo: PlayerRepository = Depends(get_player_repository),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> LineupAnalysis:
    """The best starting lineup from your roster for a week, with the closest calls.

    Roster ids that aren't in the player pool (released players, say) are skipped and listed
    in `missing_ids`, so a saved team keeps working as the pool changes.
    """
    roster, missing = [], []
    for player_id in dict.fromkeys(request.roster_ids):
        found = repo.get(player_id)
        (roster if found else missing).append(found or player_id)
    if not roster:
        raise ApiError(404, "player_not_found", "None of the roster's players are in the player pool.")
    try:
        analysis = advisor.lineup(roster, request.slots, week=request.week)
    except StartSitError as error:
        raise ApiError(422, error.code, str(error)) from None
    if missing:
        analysis.missing_ids = missing
        count = len(missing)
        analysis.warnings.append(
            f"{count} saved player{' is' if count == 1 else 's are'} no longer in the player pool and "
            f"{'was' if count == 1 else 'were'} skipped."
        )
    return analysis
