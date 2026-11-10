"""Dependency wiring. Swap the repository here to plug in a real data source."""

from functools import lru_cache

from app.data.repository import InMemoryPlayerRepository, PlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.services.trade_analyzer import TradeAnalyzer
from app.services.valuation import ValuationService


@lru_cache
def get_player_repository() -> PlayerRepository:
    return InMemoryPlayerRepository(SAMPLE_PLAYERS)


def get_trade_analyzer() -> TradeAnalyzer:
    repo = get_player_repository()
    return TradeAnalyzer(repo, ValuationService(repo))
