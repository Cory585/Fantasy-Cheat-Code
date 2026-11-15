import json

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_stat_tags
from app.data import db
from app.main import app
from app.models.player import Player, PlayerOutlook, Position
from app.models.tags import PlayerTag
from app.services.tags import BY_KEY, CATALOG, SeasonLine, TagBuilder, player_stat_tags, status_tags, tag_index

WR, RB, TE, QB = Position.WR, Position.RB, Position.TE, Position.QB


def keys(tags):
    return {t.key for t in tags}


def receiver(**kw):
    base = dict(position="WR", games=8, points=[12.0] * 8, targets=60, receptions=40, pbp_targets=60,
                middle=12, air_yards=600, target_share=[0.18] * 8, snaps=[0.85] * 8)
    base.update(kw)
    return SeasonLine(**base)


def test_catalog_is_consistent():
    assert len(BY_KEY) == len(CATALOG)
    for tag in CATALOG:
        assert tag.label and tag.description and tag.positions


@pytest.mark.parametrize(("middle", "air", "expected"), [
    (21, 480, "slot"),  # 35% between the numbers, 8.0-yard depth
    (9, 360, "slot"),  # only 15% inside, but a 6.0-yard depth is a slot game
    (6, 720, "outside"),  # 10% inside, 12-yard depth
    (15, 660, None),  # 25% inside at 11 yards: could be either
])
def test_slot_and_outside_receivers(middle, air, expected):
    tags = keys(player_stat_tags(WR, receiver(middle=middle, air_yards=air)))
    assert {"slot", "outside"} & tags == ({expected} if expected else set())


def test_alignment_needs_enough_targets():
    assert "slot" not in keys(player_stat_tags(WR, receiver(pbp_targets=10, targets=10, middle=5, air_yards=50)))


def test_alignment_is_only_for_wide_receivers():
    assert "slot" not in keys(player_stat_tags(TE, receiver(middle=30, air_yards=360)))


def test_receiver_roles_and_usage():
    line = receiver(air_yards=60 * 14, deep=18, target_share=[0.29] * 8, air_yards_share=[0.4] * 8,
                    red_zone_targets=12, end_zone_targets=8, third_down_targets=22)
    tags = player_stat_tags(WR, line)
    assert {"deep_threat", "alpha", "high_target_share", "air_yards_share", "red_zone_target",
            "end_zone_target", "third_down", "full_time"} <= keys(tags)
    detail = {t.key: t.detail for t in tags}
    assert detail["high_target_share"] == "29% of team targets"
    assert detail["deep_threat"].startswith("14.0-yard average depth")


def test_possession_and_screen_receivers():
    possession = receiver(receptions=45, air_yards=540, receiving_first_downs=27)
    assert "possession" in keys(player_stat_tags(WR, possession))
    screen = receiver(behind_los=18, air_yards=240, middle=10)
    assert "screen" in keys(player_stat_tags(WR, screen))


def test_running_back_roles():
    workhorse = SeasonLine(position="RB", games=8, points=[15.0] * 8, carries=150, team_carries=220,
                           targets=40, receptions=32, yac=290, goal_line_carries=9, team_goal_line_carries=12,
                           red_zone_carries=24, snaps=[0.72] * 8, target_share=[0.15] * 8)
    tags = keys(player_stat_tags(RB, workhorse))
    assert {"workhorse", "pass_catching_back", "goal_line_back", "red_zone_back", "yac", "high_target_share"} <= tags
    committee = SeasonLine(position="RB", games=8, points=[8.0] * 8, carries=80, team_carries=200,
                           snaps=[0.45] * 8)
    tags = keys(player_stat_tags(RB, committee))
    assert "committee" in tags and "workhorse" not in tags and "part_time" in tags


def test_quarterback_roles():
    line = SeasonLine(position="QB", games=8, points=[22.0] * 8, attempts=300, passing_air_yards=2700,
                      rushing_yards=320)
    assert {"rushing_qb", "gunslinger", "high_volume_passer", "steady"} <= keys(player_stat_tags(QB, line))


def test_trends():
    hot = receiver(points=[6, 7, 6, 8, 7, 18, 20, 19])
    assert "hot" in keys(player_stat_tags(WR, hot))
    cold = receiver(points=[18, 20, 19, 17, 18, 6, 5, 7])
    assert "cold" in keys(player_stat_tags(WR, cold))
    boom = receiver(points=[2, 30, 3, 25, 1, 28, 4, 2])
    assert "boom_bust" in keys(player_stat_tags(WR, boom))
    tds = receiver(points=[10.0] * 8, td_points=48)
    assert "td_dependent" in keys(player_stat_tags(WR, tds))


def test_snaps_rising():
    line = receiver(snaps=[0.3, 0.35, 0.3, 0.4, 0.35, 0.7, 0.75, 0.8])
    assert "snaps_rising" in keys(player_stat_tags(WR, line))


def test_status_tags():
    player = Player(id="1", name="A", position=WR, team="KC", bye_week=9, injury_status="Q")
    outlook = PlayerOutlook(provider_projection=100, adjusted_projection=100, games_remaining=8, sos_rank=2, sos_teams=32)
    tags = {t.key: t.detail for t in status_tags(player, outlook, current_week=9)}
    assert tags == {"injury": "Questionable", "on_bye": "Week 9", "easy_schedule": "#2 easiest of 32"}
    hard = outlook.model_copy(update={"sos_rank": 31})
    assert {t.key: t.detail for t in status_tags(player, hard, 10)} == {"injury": "Questionable",
                                                                         "tough_schedule": "#2 hardest of 32"}


def test_index_counts_and_orders_tags():
    players = [Player(id="1", name="A", position=WR, team="KC", injury_status="O"),
               Player(id="2", name="B", position=RB, team="KC"),
               Player(id="3", name="C", position=WR, team="KC")]
    stat = {"1": [PlayerTag(key="high_target_share"), PlayerTag(key="slot")],
            "2": [PlayerTag(key="slot")]}  # not a WR: dropped
    index = tag_index(players, stat, {}, None, 2026)
    assert [t.key for t in index.players["1"]] == ["slot", "high_target_share", "injury"]
    assert "2" not in index.players and "3" not in index.players
    counts = {t.key: t.count for t in index.tags}
    assert counts["slot"] == 1 and counts["injury"] == 1 and counts["workhorse"] == 0


def _stats(**kw):
    return json.dumps(kw)


def test_builder_reads_the_database(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert(conn, "players", [
        {"gsis_id": "00-1", "name": "Slot Guy", "position": "WR", "espn_id": 11, "pfr_id": "SlotGu00", "rookie_season": 2026},
        {"gsis_id": "00-2", "name": "Vet", "position": "WR", "espn_id": 22, "pfr_id": "VetXx00", "rookie_season": 2015},
    ])
    week_rows, usage_rows, snap_rows = [], [], []
    for week in range(1, 6):
        for gsis, share in (("00-1", 0.25), ("00-2", 0.1)):
            week_rows.append({"gsis_id": gsis, "season": 2026, "week": week, "season_type": "REG", "name": gsis,
                              "position": "WR", "team": "KC", "fantasy_points": 8, "fantasy_points_ppr": 12,
                              "stats": _stats(targets=8, receptions=6, target_share=share, receiving_yards=60)})
            usage_rows.append({"game_id": f"g{week}", "gsis_id": gsis, "season": 2026, "week": week,
                               "season_type": "REG", "team": "KC", "targets": 8, "targets_middle": 4 if gsis == "00-1" else 1,
                               "air_yards": 56 if gsis == "00-1" else 104})
        snap_rows.append({"pfr_id": "SlotGu00", "game_id": f"2026_0{week}_KC_LV", "season": 2026, "week": week,
                          "name": "Slot Guy", "position": "WR", "team": "KC", "offense_pct": 0.9})
    db.upsert(conn, "player_week_stats", week_rows)
    db.upsert(conn, "player_game_usage", usage_rows)
    db.upsert(conn, "snap_counts", snap_rows)
    db.upsert(conn, "espn_player_market", [{"espn_id": 22, "snapshot_date": "2026-11-09", "percent_change": 0.6}])
    players = [Player(id="p1", name="Slot Guy", position=WR, team="KC", espn_id=11),
               Player(id="p2", name="Vet", position=WR, team="KC", espn_id=22),
               Player(id="p3", name="Nobody", position=WR, team="KC", espn_id=33)]
    tags = TagBuilder(conn, 2026).build(players)
    assert {"slot", "high_target_share", "full_time", "rookie"} <= keys(tags["p1"])
    assert {"outside", "trending_adds"} <= keys(tags["p2"]) and "rookie" not in keys(tags["p2"])
    assert "p3" not in tags


def test_builder_handles_a_database_without_usage(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("DROP TABLE player_game_usage")
    assert TagBuilder(conn, 2026).build([Player(id="1", name="A", position=WR, team="KC", espn_id=1)]) == {}


client = TestClient(app)


def test_tags_endpoint():
    app.dependency_overrides[get_stat_tags] = lambda: ({"wr1": [PlayerTag(key="slot", detail="40% inside")]}, 2026)
    body = client.get("/tags").json()
    assert body["season"] == 2026
    assert body["players"]["wr1"] == [{"key": "slot", "detail": "40% inside"}]
    slot = next(t for t in body["tags"] if t["key"] == "slot")
    assert slot["count"] == 1 and slot["category"] == "role" and slot["positions"] == ["WR"]
    assert client.get("/tags", params={"player": "wr1"}).json()["players"] == {"wr1": body["players"]["wr1"]}
    assert client.get("/tags", params={"player": "nobody"}).json()["players"] == {}
    assert client.get("/tags", params={"player": "bad id!"}).status_code == 422


def test_tags_endpoint_without_data():
    body = client.get("/tags").json()
    assert body["season"] is None
    assert len(body["tags"]) == len(CATALOG)
