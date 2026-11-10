import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.data.adp import load_adp
from app.data.sample_players import SAMPLE_PLAYERS
from app.main import app
from app.models.player import Player, Position
from app.services.draft import DraftPlanner, snake_picks


def test_snake_order_reverses_in_even_rounds():
    picks = snake_picks(teams=12, slot=5, rounds=4)
    assert [p.label for p in picks] == ["1.05", "2.08", "3.05", "4.08"]
    assert [p.overall for p in picks] == [5, 20, 29, 44]


def test_snake_order_at_the_turn():
    assert [p.overall for p in snake_picks(teams=10, slot=10, rounds=3)] == [10, 11, 30]
    assert [p.overall for p in snake_picks(teams=10, slot=1, rounds=3)] == [1, 20, 21]


@pytest.mark.parametrize("slot", [0, 13])
def test_slot_out_of_range(slot):
    with pytest.raises(ValueError):
        snake_picks(teams=12, slot=slot, rounds=1)


def test_targets_fall_back_to_projection_order():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=2, rounds=2, targets_per_pick=2)
    assert plan.adp_source == "projection"
    first = plan.picks[0]
    # Pick 2 overall: the top projected player (qb1, 210) is assumed gone.
    assert [t.player_id for t in first.targets] == ["qb2", "rb1"]
    assert [t.rank for t in first.targets] == [2, 3]


def test_targets_use_adp_before_projection():
    players = [
        Player(id="a", name="A", position=Position.RB, team="X", ros_projection=100, espn_id=1),
        Player(id="b", name="B", position=Position.WR, team="X", ros_projection=300, espn_id=2),
        Player(id="c", name="C", position=Position.TE, team="X", ros_projection=500),
    ]
    plan = DraftPlanner(players, adp={1: 1.5, 2: 3.0}).plan(teams=1, slot=1, rounds=3, targets_per_pick=1)
    assert plan.adp_source == "espn"
    assert [pp.targets[0].player_id for pp in plan.picks] == ["a", "b", "c"]
    assert plan.picks[2].targets[0].adp is None


def test_late_picks_past_the_player_pool_have_no_targets():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=12, slot=1, rounds=3)
    assert plan.picks[-1].targets == []


# ---------- ADP loading ----------

def test_load_adp_uses_latest_snapshot_and_skips_missing():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE espn_player_market (espn_id INTEGER, snapshot_date TEXT, adp REAL)")
    conn.executemany("INSERT INTO espn_player_market VALUES (?, ?, ?)", [
        (1, "2026-11-08", 9.0), (1, "2026-11-09", 3.5), (2, "2026-11-09", 0.0), (3, "2026-11-09", None),
    ])
    assert load_adp(conn) == {1: 3.5}


def test_load_adp_without_market_table():
    assert load_adp(sqlite3.connect(":memory:")) == {}


# ---------- API ----------

client = TestClient(app)


def test_draft_plan_endpoint():
    resp = client.get("/draft/plan", params={"teams": 4, "slot": 3, "rounds": 3, "targets": 2})
    assert resp.status_code == 200
    plan = resp.json()
    assert [p["pick"]["label"] for p in plan["picks"]] == ["1.03", "2.02", "3.03"]
    assert len(plan["picks"][0]["targets"]) == 2
    assert plan["adp_source"] == "projection"


@pytest.mark.parametrize("params", [
    {"teams": 10, "slot": 11}, {"teams": 12, "slot": 0}, {"teams": 2, "slot": 1}, {"slot": 1, "rounds": 99},
])
def test_draft_plan_rejects_bad_settings(params):
    assert client.get("/draft/plan", params=params).status_code == 422


def test_plan_includes_mock_board_and_gaps():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=2, rounds=2, targets_per_pick=1)
    # Board covers every pick (4 teams x 2 rounds), in rank order.
    assert [t.rank for t in plan.board] == list(range(1, 9))
    first, second = plan.picks
    # Pick 2, then pick 7: 4 picks by other teams in between.
    assert first.picks_until_next == 4 and second.picks_until_next is None
    # Players ranked 2-6 go between your picks: qb2, rb1, wr1, wr2, rb2 by projection.
    assert first.position_mix == {Position.QB: 1, Position.RB: 2, Position.WR: 2}
    assert sum(second.position_mix.values()) == 1


def test_board_stops_at_the_player_pool():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=12, slot=1, rounds=3)
    assert len(plan.board) == len(SAMPLE_PLAYERS)
    assert plan.picks[-1].position_mix == {}
