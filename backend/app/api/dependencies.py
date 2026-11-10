"""Dependency wiring. Swap the repository here to plug in a different data source."""

import logging
import sqlite3
import threading
from pathlib import Path

from fastapi import Depends

from app.config import settings
from app.data.repository import (
    InMemoryPlayerRepository,
    PlayerRepository,
    load_cache_metadata,
    load_player_cache,
)
from app.data.sample_players import SAMPLE_PLAYERS
from app.models.player import PlayerOutlook
from app.models.trade import ScoringFormat
from app.data.adp import load_adp
from app.data.profile_cache import ProfileCache
from app.data.sources.espn_athletes import EspnAthleteSource
from app.data.sources.http import RateLimitedClient
from app.services.draft import DraftPlanner
from app.services.player_profile import LocalPlayerData
from app.services.profiles import ProfileService
from app.services.outlook import OutlookBuilder
from app.services.trade_analyzer import TradeAnalyzer
from app.services.valuation import ValuationService

log = logging.getLogger(__name__)


def _mtime(*paths: Path) -> float | None:
    """Latest modification time across paths (None if none exist)."""
    times = [p.stat().st_mtime for p in paths if p.exists()]
    return max(times) if times else None


class ReloadingPlayerData:
    """Players from the sync cache, plus schedule/form outlooks from the collected
    database. Both reload when their files change, so a fresh `app.data.sync` or
    `app.data.collect` shows up without restarting the server."""

    def __init__(self, cache_path: Path, database_path: Path | None = None):
        self._cache_path = cache_path
        self._db_paths = (database_path, database_path.with_name(database_path.name + "-wal")) if database_path else ()
        self._lock = threading.Lock()
        self._versions: tuple | None = None
        self._repo: PlayerRepository | None = None
        self._outlooks: dict[str, PlayerOutlook] = {}
        self._adp: dict[int, float] = {}

    def current(self) -> tuple[PlayerRepository, dict[str, PlayerOutlook]]:
        with self._lock:
            self._refresh()
            return self._repo, self._outlooks

    def adp(self) -> dict[int, float]:
        with self._lock:
            self._refresh()
            return self._adp

    def _refresh(self) -> None:
        versions = (_mtime(self._cache_path), _mtime(*self._db_paths))
        if self._repo is None or versions != self._versions:
            self._reload(versions)

    def _reload(self, versions: tuple) -> None:
        try:
            players = load_player_cache(self._cache_path)
        except (OSError, ValueError) as error:
            # Keep serving the previous data if the new file can't be read.
            log.error("Couldn't load player cache %s: %s", self._cache_path, error)
            if self._repo is not None:
                return
            players = None
        if players is None:
            log.warning("No player cache at %s; using sample data. Run `python -m app.data.sync`.", self._cache_path)
            self._repo, self._outlooks = InMemoryPlayerRepository(SAMPLE_PLAYERS), {}
        else:
            self._repo = InMemoryPlayerRepository(players)
            self._outlooks = self._build_outlooks(players)
            log.info("Loaded %d players (%d with schedule/form outlooks)", len(players), len(self._outlooks))
        self._adp = self._load_adp()
        self._versions = versions

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

    def _build_outlooks(self, players) -> dict[str, PlayerOutlook]:
        if not self._db_paths or not self._db_paths[0].exists():
            log.info("No database at %s; trade values use projections only. Run `python -m app.data.collect`.",
                     self._db_paths[0] if self._db_paths else None)
            return {}
        try:
            meta = load_cache_metadata(self._cache_path)
            conn = sqlite3.connect(f"file:{self._db_paths[0]}?mode=ro", uri=True)
            try:
                return OutlookBuilder(
                    conn,
                    season=int(meta["season"]),
                    scoring=ScoringFormat(meta.get("scoring", settings.default_scoring)),
                    end_week=int(meta.get("weeks", [None, 17])[1]),
                    form_weight=settings.form_weight,
                ).build(players)
            finally:
                conn.close()
        except (sqlite3.Error, KeyError, ValueError) as error:
            log.error("Couldn't build outlooks from %s: %s", self._db_paths[0], error)
            return {}


_data = ReloadingPlayerData(settings.player_cache_path, settings.database_path)


def get_player_repository() -> PlayerRepository:
    """Synced Sleeper/ESPN data if `python -m app.data.sync` has been run, else sample data."""
    return _data.current()[0]


def get_outlooks() -> dict[str, PlayerOutlook]:
    """Schedule and form outlooks by player id (empty without a collected database)."""
    return _data.current()[1]


def get_adp() -> dict[int, float]:
    """Latest ESPN average draft position by ESPN id (empty without a collected database)."""
    return _data.adp()


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
                max_response_bytes=2 * 1024 * 1024,
            )
            _profile_service = ProfileService(
                EspnAthleteSource(client),
                ProfileCache(settings.profile_cache_path),
                ttl=settings.profile_ttl_hours * 3600,
            )
        return _profile_service


def get_local_player_data() -> LocalPlayerData:
    return LocalPlayerData(settings.database_path)


def get_trade_analyzer(
    repo: PlayerRepository = Depends(get_player_repository),
    outlooks: dict[str, PlayerOutlook] = Depends(get_outlooks),
) -> TradeAnalyzer:
    return TradeAnalyzer(repo, ValuationService(repo, outlooks))
