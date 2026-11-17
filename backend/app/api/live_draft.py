"""Follow an ESPN league's draft live.

    POST   /draft/live                     start a session (reads the draft once to check access)
    GET    /draft/live/{session_id}        the draft now
    GET    /draft/live/{session_id}/events Server-Sent Events: a `draft` event whenever a pick
                                           comes in, until the draft is complete
    DELETE /draft/live/{session_id}        stop, and forget the league's cookies

The session id is the only key to a session, so treat it like a password for that draft.
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_live_drafts, get_player_repository
from app.api.errors import ApiError
from app.config import settings
from app.data.repository import PlayerRepository
from app.data.sources.espn_draft import EspnCredentials, EspnDraft, LeagueNotFound, LeaguePrivate
from app.models.live_draft import LiveDraft, LiveDraftRequest, LiveDraftSession
from app.services.live_draft import LiveDrafts, Session, TooManySessions, to_live_draft

log = logging.getLogger(__name__)

router = APIRouter(prefix="/draft/live", tags=["draft"])

SessionId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{32}$")]
Drafts = Annotated[LiveDrafts, Depends(get_live_drafts)]
Players = Annotated[PlayerRepository, Depends(get_player_repository)]

PRIVATE = ("espn_private_league",
           "ESPN says this league is private. Add your espn_s2 and SWID cookies, or check that they're current.")
NOT_FOUND = ("espn_league_not_found", "ESPN has no league with that id for that season.")
UPSTREAM = ("espn_unavailable", "Couldn't read the draft from ESPN. Try again in a moment.")
ENDED = ("live_draft_not_found", "That live draft session has ended. Connect the league again.")

# Reads of a session's draft wait on ESPN (and on the shared ESPN rate), so they get their
# own threads: a backlog of them can't hold up connecting a league or anything else that
# uses the default executor.
_snapshot_threads = ThreadPoolExecutor(max_workers=64, thread_name_prefix="live-draft")


async def _snapshot(drafts: LiveDrafts, session: Session) -> EspnDraft:
    return await asyncio.get_running_loop().run_in_executor(_snapshot_threads, drafts.snapshot, session)


def _espn_error(error: Exception) -> ApiError:
    if isinstance(error, LeaguePrivate):
        return ApiError(403, *PRIVATE)
    if isinstance(error, LeagueNotFound):
        return ApiError(404, *NOT_FOUND)
    log.warning("ESPN live draft read failed: %s", type(error).__name__)
    return ApiError(502, *UPSTREAM)


def _session(drafts: LiveDrafts, session_id: str) -> Session:
    session = drafts.get(session_id)
    if session is None:
        raise ApiError(404, *ENDED)
    return session


@router.post("", response_model=LiveDraftSession, status_code=201)
async def start_live_draft(body: LiveDraftRequest, drafts: Drafts, players: Players) -> LiveDraftSession:
    """Start following a league's draft. Public leagues need only the league id."""
    credentials = EspnCredentials(espn_s2=body.espn_s2, swid=body.swid) if body.espn_s2 and body.swid else None
    season = body.season or date.today().year
    try:
        session, draft = await asyncio.to_thread(drafts.create, body.league_id, season, credentials)
    except TooManySessions:
        raise ApiError(503, "live_draft_busy", "Too many drafts are being followed right now. Try again later.") from None
    except (LeaguePrivate, LeagueNotFound, httpx.HTTPError, ValueError) as error:
        raise _espn_error(error) from None
    return LiveDraftSession(session_id=session.id,
                            draft=to_live_draft(draft, players.list(), session.updated_at))


@router.get("/{session_id}", response_model=LiveDraft)
async def get_live_draft(session_id: SessionId, drafts: Drafts, players: Players) -> LiveDraft:
    session = _session(drafts, session_id)
    try:
        draft = await _snapshot(drafts, session)
    except (LeaguePrivate, LeagueNotFound, httpx.HTTPError, ValueError) as error:
        raise _espn_error(error) from None
    return to_live_draft(draft, players.list(), session.updated_at)


@router.delete("/{session_id}", status_code=204)
def stop_live_draft(session_id: SessionId, drafts: Drafts) -> Response:
    drafts.remove(session_id)
    return Response(status_code=204)


def _event(name: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {name}\ndata: {payload}\n\n"


@router.get("/{session_id}/events", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}}})
async def live_draft_events(session_id: SessionId, drafts: Drafts, players: Players) -> StreamingResponse:
    """A `draft` event (the LiveDraft JSON) now and whenever a pick comes in or the status
    changes; `problem` events ({code, detail}) when ESPN can't be read. The stream ends once
    the draft is complete, the cookies stop working, the session ends, or it has run its
    maximum length."""
    session = _session(drafts, session_id)
    pool = players.list()
    deadline = time.monotonic() + settings.live_draft_stream_hours * 3600

    async def stream():
        yield f"retry: {int(drafts.poll_seconds * 1000)}\n\n"
        last = None
        while True:
            # Disconnecting (or expiry) ends the stream too, so ESPN stops being read with the cookies.
            if not drafts.is_active(session):
                yield _event("problem", {"code": ENDED[0], "detail": ENDED[1]})
                return
            try:
                draft = await _snapshot(drafts, session)
            except (LeaguePrivate, LeagueNotFound) as error:
                api_error = _espn_error(error)
                yield _event("problem", {"code": api_error.code, "detail": api_error.detail})
                return
            except (httpx.HTTPError, ValueError) as error:
                api_error = _espn_error(error)
                yield _event("problem", {"code": api_error.code, "detail": api_error.detail})
            else:
                key = (draft.status, len(draft.picks), draft.picks[-1].overall if draft.picks else 0)
                if key != last:
                    last = key
                    yield _event("draft", to_live_draft(draft, pool, session.updated_at).model_dump_json())
                else:
                    yield ": waiting\n\n"  # keeps proxies from closing an idle stream
                if draft.status == "complete":
                    return
            if time.monotonic() > deadline:
                return
            await asyncio.sleep(drafts.poll_seconds)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
