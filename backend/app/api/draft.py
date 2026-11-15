from fastapi import APIRouter, Depends, Query

from app.api.errors import ApiError

from app.api.dependencies import get_draft_planner
from app.models.draft import DraftPlan
from app.services.draft import DraftPlanner

router = APIRouter(prefix="/draft", tags=["draft"])

MAX_PICKS = 20 * 25  # the largest draft the planner accepts


@router.get("/plan", response_model=DraftPlan)
def draft_plan(
    teams: int = Query(12, ge=4, le=20, description="Number of teams in the league"),
    slot: int = Query(..., ge=1, description="Your draft slot (1 = first pick)"),
    rounds: int = Query(15, ge=1, le=25),
    targets: int = Query(5, ge=1, le=20, description="Players to suggest per pick"),
    drafted: list[str] = Query([], description="Ids of players already drafted (repeat the parameter)"),
    picks_made: int = Query(0, ge=0, le=MAX_PICKS, description="Picks already made, including players not in the pool"),
    planner: DraftPlanner = Depends(get_draft_planner),
) -> DraftPlan:
    """Your picks in a snake draft, with the players likely to be available at each."""
    if slot > teams:
        raise ApiError(422, "invalid_draft_slot", f"Draft slot must be between 1 and {teams}.")
    if len(drafted) > MAX_PICKS or any(len(player_id) > 64 for player_id in drafted):
        raise ApiError(422, "invalid_drafted", f"List at most {MAX_PICKS} drafted player ids.")
    return planner.plan(teams=teams, slot=slot, rounds=rounds, targets_per_pick=targets,
                        drafted=frozenset(drafted), picks_made=picks_made)
