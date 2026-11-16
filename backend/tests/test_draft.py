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
    # ADP order (a before b); c has no ADP but the top projection, so it slots in after the
    # first-ranked ADP player instead of falling to the end.
    assert [pp.targets[0].player_id for pp in plan.picks] == ["a", "c", "b"]
    assert plan.picks[1].targets[0].adp is None


def _pool(n_drafted, n_floor):
    players, adp = [], {}
    for i in range(n_drafted):
        players.append(Player(id=f"d{i}", name=f"D{i}", position=Position.RB, team="X",
                              ros_projection=200 - i, espn_id=i + 1))
        adp[i + 1] = 1.0 + i
    for j in range(n_floor):
        espn_id = 1000 + j
        # Undrafted players all parked near ESPN's ceiling, in reverse projection order.
        players.append(Player(id=f"u{j}", name=f"U{j}", position=Position.QB, team="X",
                              ros_projection=float(j), espn_id=espn_id))
        adp[espn_id] = 170.0 - j * 0.001
    return players, adp


def test_espn_undrafted_floor_is_ordered_by_projection():
    players, adp = _pool(n_drafted=10, n_floor=30)
    board = DraftPlanner(players, adp).plan(teams=1, slot=1, rounds=1, targets_per_pick=len(players)).picks[0].targets
    tail = [t for t in board if t.player_id.startswith("u")]
    assert [t.projection for t in tail] == sorted((t.projection for t in tail), reverse=True)
    assert all(t.adp is None for t in tail)  # the floor value isn't shown as a real ADP


def test_a_few_high_adp_players_are_not_a_floor():
    players, adp = _pool(n_drafted=10, n_floor=3)
    board = DraftPlanner(players, adp).plan(teams=1, slot=1, rounds=1, targets_per_pick=len(players)).picks[0].targets
    assert board[-1].adp is not None


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
    # You take qb2 at pick 2; others take ranks 3-6: rb1, wr1, wr2, rb2 by projection.
    assert first.position_mix == {Position.RB: 2, Position.WR: 2}
    assert second.position_mix == {}  # last pick


def test_back_to_back_picks_have_an_empty_mix():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=4, rounds=2)
    assert plan.picks[0].picks_until_next == 0
    assert plan.picks[0].position_mix == {}


def test_board_stops_at_the_player_pool():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=12, slot=1, rounds=3)
    assert len(plan.board) == len(SAMPLE_PLAYERS)
    assert plan.picks[-1].position_mix == {}


# ---------- Live drafts (players already taken) ----------

def test_drafted_players_leave_the_board_and_past_picks_drop_out():
    # 4 teams, slot 2: picks 2 and 7. Picks 1-3 are in, so pick 2 is past.
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=2, rounds=2, targets_per_pick=2,
                                             drafted=frozenset({"rb1", "qb1", "wr2"}), picks_made=3)
    assert plan.picks_made == 3
    assert [p.pick.overall for p in plan.picks] == [7]
    taken = {"rb1", "qb1", "wr2"}
    assert not taken & {t.player_id for t in plan.board}
    assert len(plan.board) == 8 - 3  # one mock entry per pick still to come
    # Pick 7 is the 4th pick from now, so the top 3 remaining players are assumed gone first.
    assert plan.picks[0].targets[0].player_id == plan.board[3].player_id
    assert [t.rank for t in plan.board] == list(range(1, 6))


def test_picks_made_counts_players_outside_the_pool():
    # Two picks are in, but only one drafted player is in the pool.
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=3, rounds=1, targets_per_pick=1,
                                             drafted=frozenset({"qb1", "not-in-pool"}), picks_made=2)
    assert plan.picks_made == 2
    assert plan.picks[0].targets[0].player_id == plan.board[0].player_id != "qb1"


def test_picks_made_is_at_least_the_drafted_players_found():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=4, rounds=1, drafted=frozenset({"qb1", "qb2"}))
    assert plan.picks_made == 2


def test_finished_draft_has_no_picks_left():
    plan = DraftPlanner(SAMPLE_PLAYERS).plan(teams=4, slot=1, rounds=1, picks_made=4)
    assert (plan.picks, plan.board) == ([], [])


def test_draft_plan_endpoint_takes_drafted_players():
    resp = client.get("/draft/plan", params=[("teams", 4), ("slot", 2), ("rounds", 2), ("picks_made", 3),
                                             ("drafted", "rb1"), ("drafted", "qb1"), ("drafted", "wr2")])
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["picks_made"] == 3
    assert [p["pick"]["overall"] for p in plan["picks"]] == [7]
    assert "qb1" not in {t["player_id"] for t in plan["board"]}


@pytest.mark.parametrize("params", [
    [("slot", 1), ("picks_made", -1)],
    [("slot", 1), ("picks_made", 501)],
    [("slot", 1)] + [("drafted", str(i)) for i in range(501)],
    [("slot", 1), ("drafted", "x" * 65)],
])
def test_draft_plan_rejects_bad_drafted_lists(params):
    assert client.get("/draft/plan", params=params).status_code == 422


# ---------- Tiers and tier alerts ----------

def _tier_pool():
    # ADP order: a to g. RBs a/b are one tier and d/f/g the next; WRs c/e share a tier.
    rows = [("a", Position.RB, 300), ("b", Position.RB, 298), ("c", Position.WR, 250),
            ("d", Position.RB, 200), ("e", Position.WR, 249), ("f", Position.RB, 198), ("g", Position.RB, 196)]
    players = [Player(id=pid, name=pid.upper(), position=pos, team="X", ros_projection=pts, espn_id=i + 1)
               for i, (pid, pos, pts) in enumerate(rows)]
    return players, {i + 1: float(i + 1) for i in range(len(rows))}


def test_targets_and_board_carry_tiers():
    players, adp = _tier_pool()
    plan = DraftPlanner(players, adp).plan(teams=2, slot=1, rounds=2, targets_per_pick=5)
    tiers = {t.player_id: t.tier for t in plan.board}
    assert tiers == {"a": 1, "b": 1, "c": 1, "d": 2}
    assert plan.picks[0].targets[4].tier == 1  # e, past the board's 4 picks but still tiered


def test_alerts_flag_tiers_gone_by_your_next_pick():
    players, adp = _tier_pool()
    # 2 teams, slot 1: picks 1, 4, 5.
    first, second, last = DraftPlanner(players, adp).plan(teams=2, slot=1, rounds=3).picks
    # At pick 1 both tier-1 RBs are there, and both are gone by pick 4 (you take a, the
    # other team b). One of the two WRs (e) should still be there.
    assert [(a.position, a.tier, a.left, a.left_at_next) for a in first.tier_alerts] == [
        (Position.RB, 1, 2, 0), (Position.WR, 1, 2, 1)]
    # At pick 4 three tier-2 RBs are left and two last to pick 5; WR e is still there too.
    assert second.tier_alerts == []
    assert last.tier_alerts == []  # no next pick to warn about


def test_back_to_back_picks_only_lose_the_player_you_take():
    players, adp = _tier_pool()
    # 2 teams, slot 2: picks 2 and 3 back to back.
    first = DraftPlanner(players, adp).plan(teams=2, slot=2, rounds=2).picks[0]
    # At pick 2 (a is gone): you take b, the last tier-1 RB, then pick again right away.
    assert [(a.position, a.tier, a.left, a.left_at_next) for a in first.tier_alerts] == [(Position.RB, 1, 1, 0)]


def test_tiers_stay_fixed_as_players_are_drafted():
    players, adp = _tier_pool()
    plan = DraftPlanner(players, adp).plan(teams=2, slot=1, rounds=3, drafted=frozenset({"a", "b"}), picks_made=2)
    # The tier-1 RBs are gone, so the best RB left is tier 2 (it isn't renumbered as 1).
    assert plan.picks[0].targets[0].player_id == "d"
    assert plan.picks[0].targets[0].tier == 2


def test_planner_tiers_depend_on_league_size():
    players, adp = _tier_pool()
    planner = DraftPlanner(players, adp)
    assert planner.tiers(2) is planner.tiers(2)  # cached
    assert set(planner.tiers(2)) == {"a", "b", "c", "d", "e", "f", "g"}
    assert set(planner.tiers(1)) <= set(planner.tiers(2))  # a smaller league tiers fewer players
