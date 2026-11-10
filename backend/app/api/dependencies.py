"""Dependency wiring. Swap the repository here to plug in a different data source."""

import logging
from functools import lru_cache

from fastapi import Depends

from app.config import settings
from app.data.repository import InMemoryPlayerRepository, PlayerRepository, load_player_cache
from app.data.sample_players import SAMPLE_PLAYERS
from app.services.trade_analyzer import TradeAnalyzer
from app.services.valuation import ValuationService

log = logging.getLogger(__name__)


@lru_cache
def get_player_repository() -> PlayerRepository:
    """Synced Sleeper/ESPN data if `python -m app.data.sync` has been run, else sample data."""
    players = load_player_cache(settings.player_cache_path)
    if players is None:
        log.warning(
            "No player cache at %s; using sample data. Run `python -m app.data.sync`.",
            settings.player_cache_path,
        )
        players = SAMPLE_PLAYERS
    return InMemoryPlayerRepository(players)


def get_trade_analyzer(repo: PlayerRepository = Depends(get_player_repository)) -> TradeAnalyzer:
    return TradeAnalyzer(repo, ValuationService(repo))
