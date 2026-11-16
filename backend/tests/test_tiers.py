import pytest

from app.data.repository import InMemoryPlayerRepository
from app.models.player import Player, Position
from app.services.tiers import depth, group_into_tiers, tier_breaks, TierService
from app.services.valuation import ValuationService


def sizes(values):
    breaks = tier_breaks(values)
    return [b - a for a, b in zip(breaks, breaks[1:] + [len(values)])]


# ---------- Tier breaks ----------

def test_clear_clusters_become_tiers():
    values = [100, 99, 98, 80, 79, 78, 77, 50, 49]
    assert sizes(values) == [3, 4, 2]


def test_a_smooth_run_splits_into_tiers_of_moderate_size():
    values = [100 - i for i in range(40)]
    tiers = sizes(values)
    assert sum(tiers) == 40
    assert 3 <= len(tiers) <= 8
    assert max(tiers) <= 12


def test_one_standout_gets_a_tier_of_its_own():
    values = [150] + [100 - i for i in range(12)]
    assert sizes(values)[0] == 1


def test_scale_does_not_matter():
    values = [100, 99, 98, 80, 79, 78, 77, 50, 49]
    assert tier_breaks(values) == tier_breaks([v / 37 for v in values]) == tier_breaks([v * 12 for v in values])


@pytest.mark.parametrize(("values", "expected"), [([], []), ([5.0], [0]), ([3, 3, 3, 3], [0])])
def test_edge_cases(values, expected):
    assert tier_breaks(values) == expected


def test_unsorted_values_are_rejected():
    with pytest.raises(ValueError):
        tier_breaks([1, 2, 3])


def test_group_into_tiers_keeps_items_in_order():
    items = [("a", 10), ("b", 9.5), ("c", 2), ("d", 1.5)]
    groups = group_into_tiers(items, lambda item: item[1])
    assert [[name for name, _ in g] for g in groups] == [["a", "b"], ["c", "d"]]


def test_depth_scales_with_league_size():
    assert depth(Position.RB, 12) == 60
    assert depth(Position.QB, 10) == 25
    assert depth(None, 12) == 120


# ---------- Tier service ----------

def player(pid, position, points, week_points=None, **extra):
    weekly = {11: {"sleeper": week_points}} if week_points is not None else {}
    return Player(id=pid, name=pid.upper(), position=position, team="KC", ros_projection=points,
                  weekly_projections=weekly, **extra)


def service(players):
    repo = InMemoryPlayerRepository(players)
    return TierService(players, ValuationService(repo))


POOL = [
    player("rb1", Position.RB, 300, 25), player("rb2", Position.RB, 295, 9), player("rb3", Position.RB, 200, 18),
    player("rb4", Position.RB, 195, 17), player("rb5", Position.RB, 0),
    player("wr1", Position.WR, 280, 20), player("wr2", Position.WR, 150, 11),
    player("te1", Position.TE, 180, 12, injury_status="Q", bye_week=7),
    player("qb1", Position.QB, 350, 24),
]


def test_position_tiers_by_projection():
    result = service(POOL).tiers(Position.RB, teams=12)
    assert (result.position, result.horizon, result.metric, result.week) == ("RB", "ros", "points", None)
    assert [[p.player_id for p in t.players] for t in result.tiers] == [["rb1", "rb2"], ["rb3", "rb4"]]
    first, second = result.tiers
    assert (first.high, first.low, first.drop_to_next) == (300, 295, 95)
    assert second.drop_to_next is None
    assert [p.rank for t in result.tiers for p in t.players] == [1, 2, 3, 4]


def test_players_without_points_are_left_out():
    result = service(POOL).tiers(Position.RB, teams=12)
    assert "rb5" not in {p.player_id for t in result.tiers for p in t.players}


def test_week_tiers_use_that_weeks_projection():
    result = service(POOL).tiers(Position.RB, teams=12, week=11)
    order = [p.player_id for t in result.tiers for p in t.players]
    assert order == ["rb1", "rb3", "rb4", "rb2"]
    assert result.tiers[0].players[0].points == 25


def test_week_without_projections_is_empty():
    assert service(POOL).tiers(Position.RB, teams=12, week=12).tiers == []


def test_flex_mixes_rb_wr_te_by_value_over_replacement():
    result = service(POOL).tiers(None, teams=1)
    assert (result.position, result.metric) == ("FLEX", "value_over_replacement")
    players = [p for t in result.tiers for p in t.players]
    assert {p.position for p in players} == {Position.RB, Position.WR, Position.TE}
    assert "qb1" not in {p.player_id for p in players}
    # One team: RB1, RB2, WR1, WR2, TE1 start, and flex takes the next best (RB3 at 200), so
    # replacement is RB4 (195), nobody at WR or TE (0).
    values = {p.player_id: p.value for p in players}
    assert values["rb1"] == 105 and values["wr1"] == 280 and values["te1"] == 180
    assert [p.value for p in players] == sorted(values.values(), reverse=True)


def test_depth_limits_the_list():
    result = service(POOL).tiers(Position.RB, teams=12, max_players=3)
    assert sum(len(t.players) for t in result.tiers) == 3


def test_player_details_come_through():
    te = service(POOL).tiers(Position.TE, teams=12).tiers[0].players[0]
    assert (te.injury_status, te.bye_week, te.team) == ("Q", 7, "KC")
