"""Dependency wiring. Swap the repository here to plug in a different data source."""

import logging
import sqlite3
import threading
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from fastapi import Depends

from app.config import REGULAR_SEASON_END_WEEK, settings
from app.data.repository import (
    InMemoryPlayerRepository,
    PlayerRepository,
    load_cache_metadata,
    load_player_cache,
)
from app.data.sample_players import SAMPLE_PLAYERS
from app.models.player import Player, PlayerOutlook
from app.models.trade import ScoringFormat
from app.data import db
from app.data.adp import load_adp
from app.data.league_store import LeagueStore
from app.data.profile_cache import ProfileCache
from app.data.sources.espn_athletes import EspnAthleteSource
from app.data.sources.espn_draft import EspnDraftSource
from app.data.sources.http import RateLimitedClient
from app.services.defense_breakdown import DefenseBreakdowns
from app.services.draft import DraftPlanner
from app.services.live_draft import LiveDrafts
from app.services.player_profile import LocalPlayerData
from app.services.profiles import ProfileService
from app.services.matchups import MatchupData, load_matchups
from app.services.outlook import OutlookBuilder
from app.services import season as calendar
from app.services.season import parse_date
from app.services.start_sit import StartSitAdvisor
from app.services.tiers import TierService
from app.models.tags import PlayerTag
from app.services.tags import TagBuilder
from app.services.trade_analyzer import TradeAnalyzer
from app.services.valuation import ValuationService

log = logging.getLogger(__name__)


# Weeks used when there's no synced cache to say which weeks it covers (sample data).
DEFAULT_WEEKS = (1, REGULAR_SEASON_END_WEEK)


def cache_weeks(meta: dict) -> tuple[int, int]:
    """(first, last) week the player cache covers, or DEFAULT_WEEKS."""
    weeks = meta.get("weeks") or DEFAULT_WEEKS
    try:
        return int(weeks[0]), int(weeks[1])
    except (TypeError, ValueError, IndexError):
        return DEFAULT_WEEKS


def _season(meta: dict) -> int | None:
    try:
        return int(meta["season"])
    except (KeyError, TypeError, ValueError):
        return None


def _mtime(*paths: Path) -> float | None:
    """Latest modification time across paths (None if none exist)."""
    times = []
    for p in paths:
        try:
            times.append(p.stat().st_mtime)
        except OSError:  # missing, or (like SQLite's -wal file) deleted since it was last seen
            pass
    return max(times) if times else None


@dataclass
class _Loaded:
    """One consistent set of served data. Replaced whole on a reload, never changed in place,
    so a request that read it keeps a consistent view even while the next one is built."""

    versions: tuple
    players: list[Player] | None  # as synced; None with sample data
    meta: dict
    matchups: MatchupData
    adp: dict[int, float]
    repo: PlayerRepository | None = None
    outlooks: dict[str, PlayerOutlook] = field(default_factory=dict)
    tags: dict[str, list[PlayerTag]] = field(default_factory=dict)
    as_of: calendar.WeekState | None = None  # where the season stood for the projections
    # The week state for one day, so it's worked out once a day, not on every request.
    state_cache: tuple[date, calendar.WeekState] | None = None


class ReloadingPlayerData:
    """Players from the sync cache, plus schedule/form outlooks from the collected
    database. Both reload when they change, so a fresh `app.data.sync` or `app.data.collect`
    shows up without restarting the server. The database counts as changed when a collection
    finishes (see `db.record_completed_run`), not on every commit a running collection makes.

    New data is built while the old data is still served: only the first load makes requests
    wait.

    Rest-of-season projections are summed from each player's weekly projections from the
    current week on, and recomputed (with the outlooks and tags built from them) whenever the
    week turns over or a team plays its game, so played weeks stop counting without a re-sync."""

    def __init__(self, cache_path: Path, database_path: Path | None = None,
                 model_paths: tuple[Path, Path] | None = None):
        self._cache_path = cache_path
        self._model_paths = model_paths or (None, None)
        self._db_paths = (database_path, database_path.with_name(database_path.name + "-wal")) if database_path else ()
        self._data: _Loaded | None = None
        self._lock = threading.Lock()  # held while building new data, so only one build runs at a time
        self._db_lock = threading.Lock()
        self._db_seen: tuple | None = None  # the database files' mtimes when its version was last read
        self._db_version = None

    def current(self) -> tuple[PlayerRepository, dict[str, PlayerOutlook]]:
        data = self._loaded()
        return data.repo, data.outlooks

    def adp(self) -> dict[int, float]:
        return self._loaded().adp

    def tags(self) -> tuple[dict[str, list[PlayerTag]], int | None]:
        """Stat-based player tags and the season they come from."""
        data = self._loaded()
        season = data.meta.get("season")
        return data.tags, int(season) if season is not None else None

    def matchups(self) -> tuple[MatchupData, dict]:
        """Weekly matchup data and the player cache's metadata (season, weeks, scoring)."""
        data = self._loaded()
        return data.matchups, data.meta

    def source(self) -> str:
        """"synced" when serving the sync cache, "sample" when serving the built-in sample players."""
        return "sample" if self._loaded().players is None else "synced"

    def _loaded(self) -> _Loaded:
        data = self._data
        if data is not None and not self._outdated(data):
            return data
        if data is None:
            self._lock.acquire()  # nothing to serve yet: wait for the first load
        elif not self._lock.acquire(blocking=False):
            return data  # another request is building the new data; serve the current data meanwhile
        try:
            data = self._data
            if data is None or self._outdated(data):
                data = self._data = self._rebuild(data)
            return data
        finally:
            self._lock.release()

    def _outdated(self, data: _Loaded) -> bool:
        return data.versions != self._versions() or (data.players is not None and self._week_state(data) != data.as_of)

    def _versions(self) -> tuple:
        return (_mtime(self._cache_path), self._database_version(),
                tuple(_mtime(p) for p in self._model_paths if p is not None))

    def _database_version(self):
        """Changes when a collection finishes. Databases without a finished collection recorded
        (collected before that was recorded, or built some other way) fall back to the file's
        modification time."""
        if not self._db_paths:
            return None
        seen = (_mtime(self._db_paths[0]), _mtime(self._db_paths[1]))
        with self._db_lock:
            if seen != self._db_seen:
                self._db_version = self._read_database_version(seen[0])
                # Read the mtimes again: opening a WAL database can create its -wal file.
                self._db_seen = (_mtime(self._db_paths[0]), _mtime(self._db_paths[1]))
            return self._db_version

    def _read_database_version(self, modified: float | None):
        if modified is None:
            return None
        try:
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                finished = db.last_completed_run(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            finished = None
        return ("collected", finished) if finished else ("modified", modified)

    def _rebuild(self, previous: _Loaded | None) -> _Loaded:
        versions = self._versions()
        data = previous
        if previous is None or versions != previous.versions:
            data = self._load(versions, previous)
        # Also after a failed reload, which keeps serving the previous players: they still need
        # to move on with the weeks.
        if data.players is not None and self._week_state(data) != data.as_of:
            data = self._derive(data)
        return data

    def _week_state(self, data: _Loaded) -> calendar.WeekState:
        """Where the season stands today. It only changes with the date or the data, so it's
        cached for the day with the data."""
        day = calendar.today()
        if data.state_cache is None or data.state_cache[0] != day:
            state = calendar.week_state(data.matchups, cache_weeks(data.meta), _season(data.meta),
                                        parse_date(data.meta.get("season_start")), day)
            data.state_cache = (day, state)
        return data.state_cache[1]

    def _derive(self, data: _Loaded) -> _Loaded:
        """Rest-of-season projections, outlooks, and tags for the current week. Once the season
        is over, the synced totals stand (with no outlooks: there's no schedule left)."""
        state = self._week_state(data)
        if state.over:
            players, outlooks = data.players, {}
        else:
            players = [calendar.rest_of_season(p, state.week, state.played) for p in data.players]
            outlooks = self._build_outlooks(players, data.meta, state.week, state.played)
        return replace(data, repo=InMemoryPlayerRepository(players), outlooks=outlooks,
                       tags=self._build_tags(players, data.meta), as_of=state)

    def _load(self, versions: tuple, previous: _Loaded | None) -> _Loaded:
        try:
            players = load_player_cache(self._cache_path)
        except (OSError, ValueError) as error:
            log.error("Couldn't load player cache %s: %s", self._cache_path, error)
            if previous is not None:
                # Keep serving the previous data, but remember the broken file's version so it
                # isn't re-read on every request.
                return replace(previous, versions=versions)
            players = None
        adp = self._load_adp()
        if players is None:
            log.warning("No player cache at %s; using sample data. Run `python -m app.data.sync`.", self._cache_path)
            return _Loaded(versions, None, {}, MatchupData(), adp, repo=InMemoryPlayerRepository(SAMPLE_PLAYERS))
        meta = self._load_meta()
        data = self._derive(_Loaded(versions, players, meta, self._load_matchups(meta), adp))
        log.info("Loaded %d players (%d with schedule/form outlooks) for week %d",
                 len(players), len(data.outlooks), data.as_of.week)
        return data

    def _load_meta(self) -> dict:
        try:
            return load_cache_metadata(self._cache_path)
        except (OSError, ValueError) as error:
            log.error("Couldn't read cache metadata from %s: %s", self._cache_path, error)
            return {}

    def _load_matchups(self, meta: dict) -> MatchupData:
        if not self._db_paths or not self._db_paths[0].exists() or "season" not in meta:
            return MatchupData()
        try:
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                return load_matchups(
                    conn,
                    season=int(meta["season"]),
                    scoring=ScoringFormat(meta.get("scoring", settings.default_scoring)),
                    dst_model_path=self._model_paths[0],
                    kicker_model_path=self._model_paths[1],
                )
            finally:
                conn.close()
        except (sqlite3.Error, KeyError, ValueError) as error:
            log.error("Couldn't load matchups from %s: %s", self._db_paths[0], error)
            return MatchupData()

    def _build_tags(self, players, meta: dict) -> dict[str, list[PlayerTag]]:
        if not self._db_paths or not self._db_paths[0].exists() or "season" not in meta:
            return {}
        try:
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                return TagBuilder(
                    conn, int(meta["season"]),
                    ScoringFormat(meta.get("scoring", settings.default_scoring)),
                ).build(players)
            finally:
                conn.close()
        except (sqlite3.Error, KeyError, ValueError) as error:
            log.error("Couldn't build player tags from %s: %s", self._db_paths[0], error)
            return {}

    def _load_adp(self) -> dict[int, float]:
        if not self._db_paths or not self._db_paths[0].exists():
            return {}
        try:
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                return load_adp(conn)
            finally:
                conn.close()
        except sqlite3.Error as error:
            log.error("Couldn't load ADP from %s: %s", self._db_paths[0], error)
            return {}

    def _build_outlooks(self, players, meta: dict, week: int, played: frozenset[str]) -> dict[str, PlayerOutlook]:
        if not self._db_paths or not self._db_paths[0].exists():
            log.info("No database at %s; trade values use projections only. Run `python -m app.data.collect`.",
                     self._db_paths[0] if self._db_paths else None)
            return {}
        try:
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                return OutlookBuilder(
                    conn,
                    season=int(meta["season"]),
                    scoring=ScoringFormat(meta.get("scoring", settings.default_scoring)),
                    start_week=week,
                    played=played,
                    end_week=cache_weeks(meta)[1],
                    form_weight=settings.form_weight,
                ).build(players)
            finally:
                conn.close()
        except (sqlite3.Error, KeyError, ValueError) as error:
            log.error("Couldn't build outlooks from %s: %s", self._db_paths[0], error)
            return {}


_data = ReloadingPlayerData(settings.player_cache_path, settings.database_path,
                            (settings.dst_model_path, settings.kicker_model_path))


def get_player_data() -> tuple[PlayerRepository, dict[str, PlayerOutlook]]:
    """The players and their outlooks, read together once per request, so both come from the
    same week even if it turns over mid-request."""
    return _data.current()


def get_player_repository(
    data: tuple[PlayerRepository, dict[str, PlayerOutlook]] = Depends(get_player_data),
) -> PlayerRepository:
    """Synced Sleeper/ESPN data if `python -m app.data.sync` has been run, else sample data."""
    return data[0]


def get_data_source() -> str:
    """"synced" when serving the sync cache, "sample" when serving the sample players."""
    return _data.source()


def get_outlooks(
    data: tuple[PlayerRepository, dict[str, PlayerOutlook]] = Depends(get_player_data),
) -> dict[str, PlayerOutlook]:
    """Schedule and form outlooks by player id (empty without a collected database)."""
    return data[1]


def get_adp() -> dict[int, float]:
    """Latest ESPN average draft position by ESPN id (empty without a collected database)."""
    return _data.adp()


def get_stat_tags() -> tuple[dict[str, list[PlayerTag]], int | None]:
    """Stat-based player tags (empty without a collected database) and their season."""
    return _data.tags()


def get_matchups() -> tuple[MatchupData, dict]:
    """Weekly matchup data (empty without a collected database) and the player cache metadata."""
    return _data.matchups()


def get_start_sit_advisor(data: tuple[MatchupData, dict] = Depends(get_matchups)) -> StartSitAdvisor:
    matchups, meta = data
    return StartSitAdvisor(matchups, _season(meta), cache_weeks(meta), form_weight=settings.form_weight,
                           model_weight=settings.model_weight, season_start=parse_date(meta.get("season_start")))


def get_draft_planner(
    repo: PlayerRepository = Depends(get_player_repository),
    adp: dict[int, float] = Depends(get_adp),
) -> DraftPlanner:
    return DraftPlanner(repo.list(), adp)


_profile_service: ProfileService | None = None
_profile_lock = threading.Lock()


def get_profile_service() -> ProfileService:
    """ESPN profile fetcher, created on first use. Request-time fetches use a short timeout,
    one retry, and a response size cap, so a slow ESPN can't tie up the server."""
    global _profile_service
    with _profile_lock:
        if _profile_service is None:
            client = RateLimitedClient(
                min_interval=settings.request_interval_seconds,
                max_retries=1,
                backoff_base=1.0,
                timeout=8.0,
                total_timeout=10.0,
                max_response_bytes=2 * 1024 * 1024,
                max_retry_delay=2.0,
            )
            _profile_service = ProfileService(
                EspnAthleteSource(client),
                ProfileCache(settings.profile_cache_path),
                ttl=settings.profile_ttl_hours * 3600,
            )
        return _profile_service


_defense_breakdowns = DefenseBreakdowns(settings.database_path)


def get_defense_breakdowns() -> DefenseBreakdowns:
    """Team defense breakdowns from the collected database (cached until it changes)."""
    return _defense_breakdowns


def get_local_player_data() -> LocalPlayerData:
    return LocalPlayerData(settings.database_path)


def get_trade_analyzer(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
    data: tuple[MatchupData, dict] = Depends(get_matchups),
) -> TradeAnalyzer:
    return TradeAnalyzer(repo, ValuationService(repo, outlooks), scoring=_scoring(data[1]))


def _scoring(meta: dict) -> ScoringFormat | None:
    """The scoring format the synced players were built for (None for sample data)."""
    try:
        return ScoringFormat(meta["scoring"])
    except (KeyError, ValueError):
        return None


def get_tier_service(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
) -> TierService:
    return TierService(repo.list(), ValuationService(repo, outlooks))


_league_store = LeagueStore(settings.league_db_path, max_total=settings.max_stored_leagues)


def get_league_store() -> LeagueStore:
    """The leagues saved on each profile."""
    return _league_store


_live_drafts: LiveDrafts | None = None
_live_drafts_lock = threading.Lock()


def get_live_drafts() -> LiveDrafts:
    """Live ESPN draft sessions, created on first use. Reads use a short timeout and a size
    cap; the per-host spacing is small because each session already polls only every few
    seconds, and many leagues can be drafting at once."""
    global _live_drafts
    with _live_drafts_lock:
        if _live_drafts is None:
            client = RateLimitedClient(
                min_interval=0.2,
                max_retries=1,
                backoff_base=1.0,
                timeout=8.0,
                total_timeout=10.0,
                max_response_bytes=5 * 1024 * 1024,
                max_retry_delay=2.0,
            )
            _live_drafts = LiveDrafts(
                EspnDraftSource(client),
                poll_seconds=settings.live_draft_poll_seconds,
                ttl_seconds=settings.live_draft_session_hours * 3600,
                max_sessions=settings.live_draft_max_sessions,
            )
        return _live_drafts
