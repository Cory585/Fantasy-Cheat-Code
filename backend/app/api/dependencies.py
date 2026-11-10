"""Dependency wiring. Swap the repository here to plug in a different data source."""

import logging
import threading
from pathlib import Path

from fastapi import Depends

from app.config import settings
from app.data.repository import InMemoryPlayerRepository, PlayerRepository, load_player_cache
from app.data.sample_players import SAMPLE_PLAYERS
from app.services.trade_analyzer import TradeAnalyzer
from app.services.valuation import ValuationService

log = logging.getLogger(__name__)


class ReloadingPlayerRepository:
    """Serves players from the sync cache and reloads them when the file changes,
    so a fresh `python -m app.data.sync` shows up without restarting the server."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._repo: PlayerRepository | None = None

    def current(self) -> PlayerRepository:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        with self._lock:
            if self._repo is None or mtime != self._mtime:
                self._reload(mtime)
            return self._repo

    def _reload(self, mtime: float | None) -> None:
        try:
            players = load_player_cache(self._path)
        except (OSError, ValueError) as error:
            # Keep serving the previous data if the new file can't be read.
            log.error("Couldn't load player cache %s: %s", self._path, error)
            if self._repo is not None:
                return
            players = None
        if players is None:
            log.warning("No player cache at %s; using sample data. Run `python -m app.data.sync`.", self._path)
            players = SAMPLE_PLAYERS
        else:
            log.info("Loaded %d players from %s", len(players), self._path)
        self._repo = InMemoryPlayerRepository(players)
        self._mtime = mtime


_players = ReloadingPlayerRepository(settings.player_cache_path)


def get_player_repository() -> PlayerRepository:
    """Synced Sleeper/ESPN data if `python -m app.data.sync` has been run, else sample data."""
    return _players.current()


def get_trade_analyzer(repo: PlayerRepository = Depends(get_player_repository)) -> TradeAnalyzer:
    return TradeAnalyzer(repo, ValuationService(repo))
