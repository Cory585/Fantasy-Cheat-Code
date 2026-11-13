import pytest

from app.api.dependencies import (
    get_adp,
    get_defense_breakdowns,
    get_local_player_data,
    get_matchups,
    get_outlooks,
    get_player_repository,
    get_profile_service,
)
from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.main import app, limiter
from app.services.defense_breakdown import DefenseBreakdowns
from app.services.matchups import MatchupData
from app.services.player_profile import LocalPlayerData


class NoNetworkProfiles:
    """Default profile service in tests: never contacts ESPN."""

    def get(self, espn_id):
        raise AssertionError("tests must not fetch real ESPN profiles; override get_profile_service")


@pytest.fixture(autouse=True)
def use_sample_players():
    """Keep tests deterministic regardless of whether a synced player cache exists."""
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(SAMPLE_PLAYERS)
    app.dependency_overrides[get_adp] = lambda: {}
    app.dependency_overrides[get_outlooks] = lambda: {}
    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), {})
    app.dependency_overrides[get_profile_service] = NoNetworkProfiles
    app.dependency_overrides[get_local_player_data] = lambda: LocalPlayerData(None)
    app.dependency_overrides[get_defense_breakdowns] = lambda: DefenseBreakdowns(None)
    limiter.reset()
    yield
    app.dependency_overrides.clear()
