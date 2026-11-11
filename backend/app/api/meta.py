"""Data freshness: what the API is currently serving."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_outlooks, get_player_repository, get_start_sit_advisor
from app.config import settings
from app.data.repository import PlayerRepository, load_cache_metadata
from app.models.player import PlayerOutlook
from app.services.start_sit import StartSitAdvisor

router = APIRouter(tags=["meta"])


@router.get("/meta")
def meta(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> dict:
    """Where player data comes from, when it was synced, the weeks it covers, and the current
    week (the first with games still to play), which start/sit uses by default."""
    info: dict = {
        "source": "sample", "players": len(repo.list()), "outlooks": len(outlooks),
        "weeks": list(advisor.weeks), "current_week": advisor.current_week,
        "matchups": advisor.matchups.available,
    }
    path = settings.player_cache_path
    if path.exists():
        try:
            cache = load_cache_metadata(path)
        except (OSError, ValueError):
            return info
        info.update(
            source="synced",
            season=cache.get("season"),
            scoring=cache.get("scoring"),
            synced_at=cache.get("synced_at"),
        )
    return info
