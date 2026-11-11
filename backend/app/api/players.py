from fastapi import APIRouter, Depends, Path

from app.api.errors import ApiError

from app.api.dependencies import get_local_player_data, get_player_repository, get_profile_service
from app.data.repository import PlayerRepository
from app.models.player import Player, Position
from app.models.profile import PlayerProfile
from app.services.player_profile import LocalPlayerData, build_profile
from app.services.profiles import ProfileService

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


@router.get("/{player_id}/profile", response_model=PlayerProfile)
def get_player_profile(
    player_id: str = Path(pattern=PLAYER_ID_PATTERN),
    repo: PlayerRepository = Depends(get_player_repository),
    service: ProfileService = Depends(get_profile_service),
    local: LocalPlayerData = Depends(get_local_player_data),
) -> PlayerProfile:
    """Bio, NFL and college stats (from ESPN, cached), and combine results (from nflverse).

    Only players in the app's player pool have profiles. If ESPN can't be reached, the
    profile still returns local data, and `espn.status` / `espn.code` say why ESPN data is
    missing (`upstream_busy` or `upstream_error`, with `retry_after` seconds).
    """
    player = repo.get(player_id)
    if player is None:
        raise ApiError(404, "player_not_found", f"Player '{player_id}' not found")
    return build_profile(player, service, local)
