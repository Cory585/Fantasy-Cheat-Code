from fastapi import APIRouter, Depends, Path

from app.api.errors import ApiError

from app.api.dependencies import get_player_repository
from app.data.repository import PlayerRepository
from app.models.player import Player, Position

router = APIRouter(prefix="/players", tags=["players"])

# App player ids are Sleeper ids (digits) or team abbreviations for defenses; reject anything else
# before it reaches a lookup or a log line.
PLAYER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"


@router.get("", response_model=list[Player])
def list_players(
    position: Position | None = None,
    repo: PlayerRepository = Depends(get_player_repository),
) -> list[Player]:
    return repo.list(position)


@router.get("/{player_id}", response_model=Player)
def get_player(player_id: str = Path(pattern=PLAYER_ID_PATTERN), repo: PlayerRepository = Depends(get_player_repository)) -> Player:
    player = repo.get(player_id)
    if player is None:
        raise ApiError(404, "player_not_found", f"Player '{player_id}' not found")
    return player
