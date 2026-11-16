"""Data freshness: what the API is currently serving."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_data_source, get_matchups, get_outlooks, get_player_repository, get_start_sit_advisor
from app.data.repository import PlayerRepository
from app.models.player import PlayerOutlook
from app.services import season as calendar
from app.services.matchups import MatchupData
from app.services.start_sit import StartSitAdvisor

router = APIRouter(tags=["meta"])


@router.get("/meta")
def meta(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
    source: str = Depends(get_data_source),
    data: tuple[MatchupData, dict] = Depends(get_matchups),
) -> dict:
    """Where player data comes from, when it was synced, the weeks it covers, and the current
    week (the first with games still to play, or the calendar week without a collected
    schedule), which start/sit and tiers use by default.

    `source` is what the API is actually serving: "synced" (the sync cache) or "sample" (the
    built-in sample players, used when there's no cache or it couldn't be read). Synced data
    also reports the week it was synced in (`synced_week`) and whether that's before the
    current week (`stale`): rest-of-season totals still drop played weeks, but the providers'
    projections, injuries, and the player pool are from that week."""
    current = advisor.current_week
    info: dict = {
        "source": source, "players": len(repo.list()), "outlooks": len(outlooks),
        "weeks": list(advisor.weeks), "current_week": current,
        "matchups": advisor.matchups.available, "stale": False,
    }
    if source == "synced":
        cache = data[1]
        synced_week = _synced_week(cache, advisor)
        info.update(
            season=cache.get("season"),
            scoring=cache.get("scoring"),
            synced_at=cache.get("synced_at"),
            synced_week=synced_week,
            stale=synced_week < current,
        )
    return info


def _synced_week(cache: dict, advisor: StartSitAdvisor) -> int:
    """The week the sync ran in, by the calendar (else the first week it synced)."""
    synced_on = calendar.parse_date(cache.get("synced_at"))
    if synced_on is None or advisor.season is None:
        return advisor.weeks[0]
    return calendar.week_on(synced_on, advisor.season, advisor.season_start)
