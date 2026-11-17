"""Live draft sessions: follow an ESPN league's draft while it runs.

A session holds one league's id, season, and (for a private league) the ESPN cookies, in
memory only. They're never written to disk or logged, and they go when the session is
removed or sits unused past its time to live. Reads of ESPN are shared: however many
streams or tabs follow a session, ESPN is read at most once per poll interval for it.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.data.sources.espn_draft import EspnCredentials, EspnDraft, EspnDraftSource
from app.models.live_draft import LiveDraft, LivePick
from app.models.player import Player


class TooManySessions(Exception):
    pass


@dataclass(eq=False)
class Session:
    id: str
    league_id: str
    season: int
    credentials: EspnCredentials | None = field(repr=False)
    last_used: float
    draft: EspnDraft | None = None
    fetched_at: float = 0.0  # clock time of the last ESPN read
    updated_at: datetime | None = None  # wall time of the last successful ESPN read
    error: Exception | None = field(default=None, repr=False)  # the last read's failure, if it failed
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class LiveDrafts:
    def __init__(self, source: EspnDraftSource, poll_seconds: float = 4.0, ttl_seconds: float = 6 * 3600,
                 max_sessions: int = 500, clock: Callable[[], float] = time.monotonic,
                 max_streams_per_session: int = 6, max_streams_per_client: int = 30):
        self._source = source
        self.poll_seconds = poll_seconds
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._max_streams_per_session = max_streams_per_session
        self._max_streams_per_client = max_streams_per_client
        self._session_streams: dict[str, int] = {}
        self._client_streams: dict[str, int] = {}

    def create(self, league_id: str, season: int, credentials: EspnCredentials | None) -> tuple[Session, EspnDraft]:
        """Read the draft once (so a wrong id or cookie fails now), then keep a session for it."""
        draft = self._source.draft(season, league_id, credentials)
        now = self._clock()
        session = Session(id=secrets.token_urlsafe(24), league_id=league_id, season=season,
                          credentials=credentials, last_used=now, draft=draft, fetched_at=now,
                          updated_at=datetime.now(UTC))
        with self._lock:
            self._purge(now)
            if len(self._sessions) >= self._max:
                raise TooManySessions
            self._sessions[session.id] = session
        return session, draft

    def get(self, session_id: str) -> Session | None:
        now = self._clock()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_used = now
            return session

    def is_active(self, session: Session) -> bool:
        """Whether the session is still kept (not removed or expired), without counting as a use."""
        with self._lock:
            return self._sessions.get(session.id) is session and self._clock() - session.last_used <= self._ttl

    def remove(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def open_stream(self, session: Session, client: str) -> bool:
        """Count an event stream for the session and client, unless either is at its cap."""
        with self._lock:
            if (self._session_streams.get(session.id, 0) >= self._max_streams_per_session
                    or self._client_streams.get(client, 0) >= self._max_streams_per_client):
                return False
            self._session_streams[session.id] = self._session_streams.get(session.id, 0) + 1
            self._client_streams[client] = self._client_streams.get(client, 0) + 1
            return True

    def close_stream(self, session: Session, client: str) -> None:
        with self._lock:
            for counts, key in ((self._session_streams, session.id), (self._client_streams, client)):
                left = counts.get(key, 0) - 1
                if left > 0:
                    counts[key] = left
                else:
                    counts.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def snapshot(self, session: Session) -> EspnDraft:
        """The session's draft, read from ESPN unless it was read within the poll interval.

        A failed read counts too: its error is raised again until the interval has passed,
        so an ESPN outage doesn't turn every open stream into its own request."""
        with session.lock:
            now = self._clock()
            session.last_used = now
            if session.draft is None or now - session.fetched_at >= self.poll_seconds:
                try:
                    session.draft = self._source.draft(session.season, session.league_id, session.credentials)
                except Exception as error:
                    session.error = error
                    session.fetched_at = self._clock()
                    raise
                session.error = None
                session.fetched_at = self._clock()
                session.updated_at = datetime.now(UTC)
            if session.error is not None:
                raise session.error.with_traceback(None)
            return session.draft

    def _purge(self, now: float) -> None:
        expired = [sid for sid, s in self._sessions.items() if now - s.last_used > self._ttl]
        for sid in expired:
            del self._sessions[sid]


def default_season(today: date, first_month: int) -> int:
    """The season a league id most likely belongs to: last year's before `first_month`."""
    return today.year if today.month >= first_month else today.year - 1


def to_live_draft(draft: EspnDraft, players: list[Player], updated_at: datetime | None = None) -> LiveDraft:
    """The draft with each pick matched to our player pool by ESPN id."""
    by_espn = {p.espn_id: p for p in players if p.espn_id is not None}
    picks = []
    for pick in draft.picks:
        player = by_espn.get(pick.espn_player_id)
        picks.append(LivePick(
            **pick.model_dump(),
            player_id=player.id if player else None,
            name=player.name if player else None,
            position=player.position if player else None,
            team=player.team if player else None,
        ))
    return LiveDraft(
        league_id=draft.league_id, season=draft.season, name=draft.name, status=draft.status,
        draft_type=draft.draft_type, team_count=draft.team_count, rounds=draft.rounds, teams=draft.teams,
        picks=picks, updated_at=updated_at or datetime.now(UTC),
    )
