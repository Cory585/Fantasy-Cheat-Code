"""The leagues on a profile: up to MAX_LEAGUES, each with its own lineup spots and roster.

Every request names the profile with an `X-Profile-Key` header (a random key the browser
makes and keeps; see app/data/league_store.py). Being a custom header, it can't be sent by
another site's form or image tag, and it stays out of URLs and access logs.

Making and removing leagues is rate limited per client (see app/main.py).

Saves can be checked against the version the client last saw: each league has a `version`
(also sent as the `ETag` header) that goes up with every save. A PUT with `If-Match: "<version>"`
only goes through if the league is still at that version; otherwise it's a 409 with the
current league, so two tabs or devices can't silently overwrite each other. Without
If-Match, a save replaces whatever is there, as before.
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import get_league_store
from app.api.errors import ApiError
from app.data.league_store import (
    LeagueConflict,
    LeagueLimitReached,
    LeagueStore,
    LeagueStoreError,
    StoreFull,
    owner_for,
)
from app.models.league import (
    LEAGUE_ID_PATTERN,
    MAX_LEAGUES,
    PROFILE_KEY_PATTERN,
    League,
    LeagueInput,
    LeagueList,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/leagues", tags=["leagues"])

LeagueId = Annotated[str, Path(pattern=LEAGUE_ID_PATTERN)]


def profile_owner(
    x_profile_key: Annotated[str | None, Header(description="The browser's profile key")] = None,
) -> str:
    if not x_profile_key or not re.fullmatch(PROFILE_KEY_PATTERN, x_profile_key):
        raise ApiError(401, "profile_key_required", "Send this browser's profile key in the X-Profile-Key header.")
    return owner_for(x_profile_key)


Owner = Annotated[str, Depends(profile_owner)]
Store = Annotated[LeagueStore, Depends(get_league_store)]


def _unavailable(error: LeagueStoreError) -> ApiError:
    log.error("League store failed: %s", error)
    return ApiError(503, "leagues_unavailable", "Leagues can't be loaded or saved right now. Try again in a moment.")


def _not_found() -> ApiError:
    return ApiError(404, "league_not_found", "That league isn't on this profile.")


def _tag(league: League, response: Response) -> League:
    response.headers["ETag"] = f'"{league.version}"'
    return league


def expected_version(
    if_match: Annotated[str | None, Header(description='The version last seen, e.g. "3" (from ETag)')] = None,
) -> int | None:
    """The version from an If-Match header (None without one, or for `*`)."""
    if if_match is None or if_match.strip() == "*":
        return None
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit() or len(value) > 12:
        raise ApiError(400, "invalid_if_match", 'If-Match must be a league version, e.g. "3".')
    return int(value)


@router.get("", response_model=LeagueList)
def list_leagues(owner: Owner, store: Store) -> LeagueList:
    """Every league on the profile, oldest first."""
    try:
        return LeagueList(leagues=store.list(owner))
    except LeagueStoreError as error:
        raise _unavailable(error) from error


@router.post("", response_model=League, status_code=201)
def create_league(body: LeagueInput, owner: Owner, store: Store, response: Response) -> League:
    """Add a league. A profile holds at most MAX_LEAGUES (10); past that it's a 409."""
    try:
        return _tag(store.create(owner, body.name, body.full_slots(), body.roster, MAX_LEAGUES), response)
    except LeagueLimitReached as error:
        raise ApiError(409, "league_limit",
                       f"A profile can hold up to {MAX_LEAGUES} leagues. Remove one to add another.") from error
    except StoreFull as error:
        raise ApiError(503, "leagues_full", "The server can't hold any more leagues right now.") from error
    except LeagueStoreError as error:
        raise _unavailable(error) from error


@router.get("/{league_id}", response_model=League)
def get_league(league_id: LeagueId, owner: Owner, store: Store, response: Response) -> League:
    try:
        league = store.get(owner, league_id)
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if league is None:
        raise _not_found()
    return _tag(league, response)


@router.put("/{league_id}", response_model=League, responses={409: {"description": "Saved elsewhere since If-Match"}})
def save_league(league_id: LeagueId, body: LeagueInput, owner: Owner, store: Store, response: Response,
                version: Annotated[int | None, Depends(expected_version)] = None):
    """Replace a league's name, lineup spots, and roster. With `If-Match: "<version>"`, only if
    nobody has saved it since that version: otherwise a 409 whose body includes the current
    league (`league`), so the client can show it instead."""
    try:
        league = store.update(owner, league_id, body.name, body.full_slots(), body.roster, expected_version=version)
    except LeagueConflict as conflict:
        current = conflict.current
        return JSONResponse(
            {"detail": "This league was changed somewhere else (another tab or device) since it was loaded.",
             "code": "league_conflict", "league": current.model_dump()},
            status_code=409, headers={"ETag": f'"{current.version}"'},
        )
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if league is None:
        raise _not_found()
    return _tag(league, response)


@router.delete("/{league_id}", status_code=204)
def delete_league(league_id: LeagueId, owner: Owner, store: Store) -> Response:
    try:
        removed = store.delete(owner, league_id)
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if not removed:
        raise _not_found()
    return Response(status_code=204)
