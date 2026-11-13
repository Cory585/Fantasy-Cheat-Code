from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies import get_defense_breakdowns, get_start_sit_advisor
from app.api.errors import ApiError
from app.models.defense import DefenseBreakdown, DefenseLeague
from app.services.defense_breakdown import DefenseBreakdowns, LeagueDefenses, NoDefenseData, upcoming_games
from app.services.start_sit import StartSitAdvisor

router = APIRouter(prefix="/defenses", tags=["defenses"])

SEASON = Query(None, ge=1999, le=2100, description="Default: the latest season with team stats")


def _league(breakdowns: DefenseBreakdowns, season: int | None) -> LeagueDefenses:
    try:
        return breakdowns.get(season)
    except NoDefenseData as error:
        raise ApiError(404, "no_defense_data", str(error)) from None


@router.get("", response_model=DefenseLeague)
def league(
    season: int | None = SEASON,
    breakdowns: DefenseBreakdowns = Depends(get_defense_breakdowns),
) -> DefenseLeague:
    """Every team defense with its position-group ranks, points allowed, and DST points per game."""
    return _league(breakdowns, season).league()


@router.get("/{team}", response_model=DefenseBreakdown)
def team_breakdown(
    team: str = Path(pattern=r"^[A-Za-z]{2,3}$", description="Team abbreviation, e.g. PIT"),
    season: int | None = SEASON,
    breakdowns: DefenseBreakdowns = Depends(get_defense_breakdowns),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> DefenseBreakdown:
    """One defense, position group by position group (DL, LB, CB, S), with each stat's league
    rank, how its units hold up (pass rush, coverage, run defense, scoring), fantasy points it
    allows by position, and its next games' DST projections."""
    data = _league(breakdowns, season)
    team = team.upper()
    if team not in data.teams:
        raise ApiError(404, "team_not_found", f"No {data.season} defense stats for '{team}'.")
    upcoming = []
    if advisor.matchups.season == data.season:
        upcoming = upcoming_games(advisor.matchups, team, advisor.current_week)
    return data.breakdown(team, upcoming)
