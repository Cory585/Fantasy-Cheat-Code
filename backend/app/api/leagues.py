"""The leagues on a profile: up to MAX_LEAGUES, each with its own lineup spots and roster.

Every request names the profile with an `X-Profile-Key` header (a random key the browser
makes and keeps; see app/data/league_store.py). Being a custom header, it can't be sent by
another site's form or image tag, and it stays out of URLs and access logs.

Making and removing leagues is rate limited per client (see app/main.py).
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response

from app.api.dependencies import get_league_store
from app.api.errors import ApiError
from app.data.league_store import LeagueLimitReached, LeagueStore, LeagueStoreError, StoreFull, owner_for
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


@router.get("", response_model=LeagueList)
def list_leagues(owner: Owner, store: Store) -> LeagueList:
    """Every league on the profile, oldest first."""
    try:
        return LeagueList(leagues=store.list(owner))
    except LeagueStoreError as error:
        raise _unavailable(error) from error


@router.post("", response_model=League, status_code=201)
def create_league(body: LeagueInput, owner: Owner, store: Store) -> League:
    """Add a league. A profile holds at most MAX_LEAGUES (10); past that it's a 409."""
    try:
        return store.create(owner, body.name, body.full_slots(), body.roster, MAX_LEAGUES)
    except LeagueLimitReached as error:
        raise ApiError(409, "league_limit",
                       f"A profile can hold up to {MAX_LEAGUES} leagues. Remove one to add another.") from error
    except StoreFull as error:
        raise ApiError(503, "leagues_full", "The server can't hold any more leagues right now.") from error
    except LeagueStoreError as error:
        raise _unavailable(error) from error


@router.get("/{league_id}", response_model=League)
def get_league(league_id: LeagueId, owner: Owner, store: Store) -> League:
    try:
        league = store.get(owner, league_id)
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if league is None:
        raise _not_found()
    return league


@router.put("/{league_id}", response_model=League)
def save_league(league_id: LeagueId, body: LeagueInput, owner: Owner, store: Store) -> League:
    """Replace a league's name, lineup spots, and roster."""
    try:
        league = store.update(owner, league_id, body.name, body.full_slots(), body.roster)
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if league is None:
        raise _not_found()
    return league


@router.delete("/{league_id}", status_code=204)
def delete_league(league_id: LeagueId, owner: Owner, store: Store) -> Response:
    try:
        removed = store.delete(owner, league_id)
    except LeagueStoreError as error:
        raise _unavailable(error) from error
    if not removed:
        raise _not_found()
    return Response(status_code=204)
