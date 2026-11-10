import pytest

from app.api.dependencies import get_player_repository
from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.main import app


@pytest.fixture(autouse=True)
def use_sample_players():
    """Keep tests deterministic regardless of whether a synced player cache exists."""
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(SAMPLE_PLAYERS)
    yield
    app.dependency_overrides.clear()
