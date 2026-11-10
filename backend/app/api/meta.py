"""Data freshness: what the API is currently serving."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_outlooks, get_player_repository
from app.config import settings
from app.data.repository import PlayerRepository, load_cache_metadata
from app.models.player import PlayerOutlook

router = APIRouter(tags=["meta"])


@router.get("/meta")
def meta(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
) -> dict:
    """Where player data comes from, when it was synced, and the weeks it covers."""
    info: dict = {"source": "sample", "players": len(repo.list()), "outlooks": len(outlooks)}
    path = settings.player_cache_path
    if path.exists():
        try:
            cache = load_cache_metadata(path)
        except (OSError, ValueError):
            return info
        info.update(
            source="synced",
            season=cache.get("season"),
            weeks=cache.get("weeks"),
            scoring=cache.get("scoring"),
            synced_at=cache.get("synced_at"),
        )
    return info
