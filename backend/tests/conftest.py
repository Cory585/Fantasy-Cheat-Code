import pytest

from app.api.dependencies import (
    get_adp,
    get_data_source,
    get_defense_breakdowns,
    get_league_store,
    get_live_drafts,
    get_local_player_data,
    get_matchups,
    get_outlooks,
    get_player_repository,
    get_profile_service,
    get_stat_tags,
)
from app.data.league_store import LeagueStore
from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.main import app, limiter
from app.services.defense_breakdown import DefenseBreakdowns
from app.services.matchups import MatchupData
from app.services.player_profile import LocalPlayerData


class NoNetworkLiveDrafts:
    """Default live draft sessions in tests: never contacts ESPN."""

    def __getattr__(self, name):
        raise AssertionError("tests must not read real ESPN drafts; override get_live_drafts")


class NoNetworkProfiles:
    """Default profile service in tests: never contacts ESPN."""

    def get(self, espn_id):
        raise AssertionError("tests must not fetch real ESPN profiles; override get_profile_service")


@pytest.fixture(autouse=True)
def use_sample_players(tmp_path):
    """Keep tests deterministic regardless of whether a synced player cache exists. Leagues go
    to a fresh database per test, never the real one."""
    leagues = LeagueStore(tmp_path / "leagues.db")
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(SAMPLE_PLAYERS)
    app.dependency_overrides[get_adp] = lambda: {}
    app.dependency_overrides[get_outlooks] = lambda: {}
    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), {})
    app.dependency_overrides[get_data_source] = lambda: "sample"
    app.dependency_overrides[get_profile_service] = NoNetworkProfiles
    app.dependency_overrides[get_local_player_data] = lambda: LocalPlayerData(None)
    app.dependency_overrides[get_defense_breakdowns] = lambda: DefenseBreakdowns(None)
    app.dependency_overrides[get_league_store] = lambda: leagues
    app.dependency_overrides[get_live_drafts] = NoNetworkLiveDrafts
    app.dependency_overrides[get_stat_tags] = lambda: ({}, None)
    limiter.reset()
    yield
    app.dependency_overrides.clear()
