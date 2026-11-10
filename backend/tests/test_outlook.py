import json

import pytest

from app.data import db
from app.models.player import Player, Position
from app.models.trade import ScoringFormat
from app.services.outlook import FORM_CAP, OutlookBuilder, kicker_points, schedule_label

SEASON = 2026


def game(week, away, home, away_score=None, home_score=None):
    return {"game_id": f"{SEASON}_{week:02d}_{away}_{home}", "season": SEASON, "game_type": "REG", "week": week,
            "gameday": None, "gametime": None, "away_team": away, "home_team": home,
            "away_score": away_score, "home_score": home_score, "spread_line": None, "total_line": None,
            "roof": None, "surface": None, "temp": None, "wind": None, "div_game": None, "espn_id": None}


def stat(gsis, week, team, opponent, ppr, position="RB", stats=None):
    return {"gsis_id": gsis, "season": SEASON, "week": week, "season_type": "REG", "name": gsis,
            "position": position, "team": team, "opponent": opponent,
            "fantasy_points": ppr - 2, "fantasy_points_ppr": ppr, "stats": json.dumps(stats or {})}


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    # Weeks 1-4 played. SOFT allows lots of RB points, HARD allows few.
    db.upsert(conn, "games", [
        game(1, "AAA", "SOFT", 20, 30), game(2, "AAA", "HARD", 10, 17),
        game(3, "AAA", "SOFT", 24, 21), game(4, "AAA", "HARD", 3, 13),
        # Remaining: AAA plays SOFT twice; BBB plays HARD twice.
        game(5, "AAA", "SOFT"), game(6, "SOFT", "AAA"),
        game(5, "BBB", "HARD"), game(6, "HARD", "BBB"),
        game(18, "AAA", "HARD"),  # after end_week, ignored
    ])
    rows = []
    for week, opp, pts in [(1, "SOFT", 30), (2, "HARD", 10), (3, "SOFT", 30), (4, "HARD", 10)]:
        rows.append(stat("rb-a", week, "AAA", opp, pts))
    db.upsert(conn, "player_week_stats", rows)
    db.upsert(conn, "players", [{"gsis_id": "rb-a", "name": "Back A", "espn_id": 1, "position": "RB", "team": "AAA",
                                 **{k: None for k in ("status", "pfr_id", "pff_id", "birth_date", "height", "weight",
                                                      "years_of_experience", "rookie_season", "draft_year",
                                                      "draft_round", "draft_pick", "headshot_url")}}])
    return conn


def player(pid, team, proj, espn_id=None, position=Position.RB, injury=None):
    return Player(id=pid, name=pid, position=position, team=team, ros_projection=proj,
                  espn_id=espn_id, injury_status=injury)


def build(conn, players, **kw):
    return OutlookBuilder(conn, SEASON, ScoringFormat.PPR, end_week=17, **kw).build(players)


def test_remaining_schedule_and_ranks(conn):
    out = build(conn, [player("a", "AAA", 40, espn_id=1), player("b", "BBB", 40)])
    a, b = out["a"], out["b"]
    assert a.games_remaining == 2 and a.remaining_opponents == ["SOFT", "SOFT"]
    assert a.sos_factor > 1 > b.sos_factor  # AAA faces the soft defense, BBB the hard one
    assert a.sos_rank < b.sos_rank  # 1 = easiest


def test_form_blends_with_provider_and_uses_schedule(conn):
    out = build(conn, [player("a", "AAA", 40, espn_id=1)], form_weight=0.5)
    a = out["a"]
    assert a.recent_ppg == 20 and a.recent_games == 4
    expected_form = min(20 * 2 * a.sos_factor, 40 * (1 + FORM_CAP))
    assert a.form_projection == pytest.approx(expected_form, abs=0.01)
    assert a.adjusted_projection == pytest.approx(0.5 * 40 + 0.5 * expected_form, abs=0.01)


def test_form_is_capped_relative_to_provider(conn):
    # Provider expects far less than recent form (e.g. starter returning): form is capped.
    a = build(conn, [player("a", "AAA", 10, espn_id=1)])["a"]
    assert a.form_projection == pytest.approx(10 * (1 + FORM_CAP))


def test_no_form_for_injured_unlinked_or_defense_players(conn):
    out = build(conn, [
        player("hurt", "AAA", 40, espn_id=1, injury="IR"),
        player("unlinked", "AAA", 40, espn_id=999),
        player("dst", "AAA", 40, position=Position.DST),
    ])
    for pid in ("hurt", "unlinked", "dst"):
        assert out[pid].form_projection is None
        assert out[pid].adjusted_projection == 40


def test_defense_schedule_uses_opponent_scoring(conn):
    # AAA's offense scored little, so defenses facing AAA have an easier schedule.
    out = build(conn, [player("soft-dst", "SOFT", 50, position=Position.DST),
                       player("hard-dst", "HARD", 50, position=Position.DST)])
    assert out["soft-dst"].sos_factor is not None


def test_team_with_no_remaining_games(conn):
    out = build(conn, [player("x", "ZZZ", 40)])
    assert out["x"].games_remaining == 0
    assert out["x"].sos_factor is None and out["x"].sos_rank is None


def test_kicker_points():
    assert kicker_points({"fg_made_20_29": 1, "fg_made_40_49": 1, "fg_made_50_59": 1, "pat_made": 3,
                          "fg_missed": 1, "fg_made_list": "25;44;51"}) == 3 + 4 + 5 + 3 - 1


def test_schedule_label():
    assert schedule_label(1, 32) == "easy"
    assert schedule_label(16, 32) == "average"
    assert schedule_label(32, 32) == "hard"
    assert schedule_label(None, 32) is None
