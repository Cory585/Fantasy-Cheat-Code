import hashlib

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import TypeAdapter

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

_PLAYER_LIST = TypeAdapter(list[Player])


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    # Weak comparison: a client may send the tag with or without the W/ prefix.
    return any(tag.strip().removeprefix("W/") == etag.removeprefix("W/") for tag in header.split(","))


@router.get("", response_model=list[Player])
def list_players(
    request: Request,
    position: Position | None = None,
    repo: PlayerRepository = Depends(get_player_repository),
) -> Response:
    """The player pool. Every page loads it, so it carries an ETag: send it back in
    `If-None-Match` and an unchanged pool is a bodiless 304."""
    body = _PLAYER_LIST.dump_json(repo.list(position))
    # Weak, because GZipMiddleware may re-encode the body; the tag describes the content.
    etag = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
    # no-cache: the browser keeps a copy but checks it on each use, so a new sync shows up at once.
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


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
