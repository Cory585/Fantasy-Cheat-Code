"""Player tags for quick lookup (see app/services/tags.py)."""

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_outlooks, get_player_repository, get_start_sit_advisor, get_stat_tags
from app.api.players import PLAYER_ID_PATTERN
from app.data.repository import PlayerRepository
from app.models.player import PlayerOutlook
from app.models.tags import PlayerTag, TagIndex
from app.services.start_sit import StartSitAdvisor
from app.services.tags import tag_index

router = APIRouter(prefix="/tags", tags=["players"])


@router.get("", response_model=TagIndex)
def list_tags(
    player: str | None = Query(None, pattern=PLAYER_ID_PATTERN, description="Only this player's tags"),
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
    stat_tags: tuple[dict[str, list[PlayerTag]], int | None] = Depends(get_stat_tags),
    advisor: StartSitAdvisor = Depends(get_start_sit_advisor),
) -> TagIndex:
    """Every tag (role, usage, trend, status) with what earns it and how many players have it,
    and each player's tags with the numbers behind them.

    Role, usage, and trend tags need the collected database (`python -m app.data.collect`);
    without it only status tags (injury, bye, schedule) appear.
    """
    tags, season = stat_tags
    index = tag_index(repo.list(), tags, outlooks, advisor.current_week, season)
    if player is not None:
        index.players = {player: index.players[player]} if player in index.players else {}
    return index
