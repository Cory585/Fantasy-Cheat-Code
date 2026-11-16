from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_matchups, get_player_repository
from app.data.repository import InMemoryPlayerRepository
from app.main import app
from app.services.matchups import MatchupData
from tests.test_tiers import POOL

client = TestClient(app)


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr("app.services.season.today", lambda: date(2026, 9, 20))  # week 2: before the synced weeks
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(POOL)
    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), {"season": 2026, "weeks": [11, 17]})


def test_tiers_endpoint(pool):
    resp = client.get("/tiers", params={"position": "RB"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["position"] == "RB" and body["teams"] == 12
    assert [len(t["players"]) for t in body["tiers"]] == [2, 2]


def test_tiers_endpoint_week_defaults_to_the_current_week(pool):
    body = client.get("/tiers", params={"position": "WR", "horizon": "week"}).json()
    assert (body["horizon"], body["week"]) == ("week", 11)


def test_tiers_endpoint_flex(pool):
    body = client.get("/tiers", params={"position": "FLEX", "teams": 4}).json()
    assert body["metric"] == "value_over_replacement"


@pytest.mark.parametrize("params", [
    {},
    {"position": "SUPERFLEX"},
    {"position": "rb"},
    {"position": "RB", "teams": 3},
    {"position": "RB", "depth": 0},
    {"position": "RB", "horizon": "season"},
    {"position": "RB", "week": 11},  # week needs horizon=week
    {"position": "RB", "horizon": "week", "week": 3},  # outside the synced weeks
])
def test_tiers_endpoint_rejects_bad_input(pool, params):
    assert client.get("/tiers", params=params).status_code == 422
