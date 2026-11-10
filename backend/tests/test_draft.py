import pytest

from app.data.sample_players import SAMPLE_PLAYERS
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
