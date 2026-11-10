from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_player_repository
from app.data.repository import PlayerRepository
from app.models.player import Player, Position

router = APIRouter(prefix="/players", tags=["players"])


@router.get("", response_model=list[Player])
def list_players(
    position: Position | None = None,
    repo: PlayerRepository = Depends(get_player_repository),
) -> list[Player]:
    return repo.list(position)


@router.get("/{player_id}", response_model=Player)
def get_player(player_id: str, repo: PlayerRepository = Depends(get_player_repository)) -> Player:
    player = repo.get(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player '{player_id}' not found")
    return player
