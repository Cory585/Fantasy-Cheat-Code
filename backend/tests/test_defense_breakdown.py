import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_defense_breakdowns, get_matchups
from app.data import db
from app.main import app
from app.services.defense_breakdown import DefenseBreakdowns, LeagueDefenses, NoDefenseData, rank
from app.services.matchups import Game, MatchupData
from app.services.special_teams import ModelEstimate, TeamModels
from tests.league_fixture import STRENGTH, build_league

client = TestClient(app)


def _player(gsis, team, position, week, opponent="X", ppr=0.0, **stats):
    return {"gsis_id": gsis, "season": 2025, "week": week, "season_type": "REG", "name": gsis,
            "position": position, "team": team, "opponent": opponent, "fantasy_points": ppr,
            "fantasy_points_ppr": ppr, "stats": json.dumps(stats)}


@pytest.fixture
def league_db(tmp_path):
    """The fixture league (BUF strongest, NYJ weakest) plus defensive players whose production
    follows simple rules, and a receiver line against every defense each week."""
    path = tmp_path / "league.db"
    conn = build_league(path)
    rows = []
    for week in range(1, 7):
        for team, s in STRENGTH.items():
            rows += [
                _player(f"{team}-DE", team, "DE", week, def_sacks=s, def_qb_hits=2 * s, def_tackles_for_loss=s),
                _player(f"{team}-LB", team, "LB", week, def_tackles_solo=5, def_tackle_assists=2,
                        def_tackles_for_loss=1, def_pass_defended=s % 2),
                _player(f"{team}-CB", team, "CB", week, def_pass_defended=3 - s,
                        def_interceptions=1 if s == 0 else 0),
                _player(f"{team}-S", team, "SAF", week, def_tackles_solo=3),
                # Receivers score 10 + the defense's strength in PPR points against each defense.
                _player(f"WR-{team}-{week}", "ZZZ", "WR", week, opponent=team, ppr=10.0 + s),
            ]
    db.upsert(conn, "player_week_stats", rows)
    db.upsert(conn, "teams", [{"abbrev": "BUF", "espn_id": 2, "location": "Buffalo", "name": "Bills",
                               "display_name": "Buffalo Bills", "color": "00338d", "logo_url": "https://x/buf.png"}])
    conn.commit()
    conn.close()
    return path


def groups_of(breakdown):
    return {g.key: g for g in breakdown.groups}


def units_of(breakdown):
    return {u.key: {s.key: s for s in u.stats} for u in breakdown.units}


def test_rank_shares_ties_and_respects_direction():
    values = {"A": 3.0, "B": 3.0, "C": 1.0}
    assert rank(values, "A") == rank(values, "B") == 1 and rank(values, "C") == 3
    assert rank(values, "C", lower_is_better=True) == 1 and rank(values, "A", lower_is_better=True) == 2


def test_groups_rank_on_the_stats_that_measure_their_job(league_db):
    league = DefenseBreakdowns(league_db).get()
    assert league.season == 2025 and league.through_week == 6
    buf = groups_of(league.breakdown("BUF"))
    assert buf["DL"].rank == 1 and buf["DL"].score > 0  # most sacks, QB hits, and tackles for loss
    sacks = next(s for s in buf["DL"].stats if s.key == "sacks")
    assert (sacks.value, sacks.rank, sacks.teams) == (3.0, 1, 4)
    assert buf["DL"].players[0].name == "BUF-DE" and buf["DL"].players[0].stats["sacks"] == 18
    # Corners are judged on passes defended and interceptions: NYJ's lead, BUF's trail.
    assert buf["CB"].rank == 4 and groups_of(league.breakdown("NYJ"))["CB"].rank == 1
    # Safeties only made tackles, which barely count: everyone ties.
    assert {groups_of(league.breakdown(t))["S"].rank for t in STRENGTH} == {1}


def test_players_count_for_the_team_they_played_for_that_week(league_db):
    conn = db.connect(league_db)
    conn.execute("UPDATE player_week_stats SET team = 'BUF' WHERE gsis_id = 'NYJ-CB' AND week > 3")
    conn.commit()
    conn.close()
    league = DefenseBreakdowns(league_db).get()
    buf_cbs = {p.name: p for p in groups_of(league.breakdown("BUF"))["CB"].players}
    nyj_cbs = {p.name: p for p in groups_of(league.breakdown("NYJ"))["CB"].players}
    assert buf_cbs["NYJ-CB"].games == 3 and nyj_cbs["NYJ-CB"].games == 3


def test_units_and_fantasy_points_allowed(league_db):
    league = DefenseBreakdowns(league_db).get()
    buf, nyj = league.breakdown("BUF"), league.breakdown("NYJ")
    # BUF allows (22 + 18 + 14) / 3 points a game, NYJ (26 + 22 + 18) / 3.
    points = units_of(buf)["scoring"]["points_allowed_pg"]
    assert points.value == 18.0 and points.rank == 1 and points.lower_is_better
    assert units_of(nyj)["scoring"]["points_allowed_pg"].rank == 4
    # Offenses take 4 - strength sacks on 35 dropbacks; BUF faces the sack-prone offenses.
    assert units_of(buf)["pass_rush"]["sack_rate"].value == pytest.approx((2 + 3 + 4) / 105, abs=1e-4)
    wr = next(f for f in buf.fantasy_allowed if f.position == "WR")
    assert wr.points_per_game == 13.0 and wr.rank == 4  # gives up the most to receivers here
    assert buf.summary.name == "Buffalo Bills" and buf.summary.logo_url == "https://x/buf.png"
    assert buf.summary.points_allowed_rank == 1


def test_league_lists_every_team_best_first(league_db):
    summary = DefenseBreakdowns(league_db).get().league()
    assert [t.overall_rank for t in summary.teams] == sorted(t.overall_rank for t in summary.teams)
    assert {t.team for t in summary.teams} == set(STRENGTH)
    assert set(summary.teams[0].group_ranks) == {"DL", "LB", "CB", "S"}


def test_breakdowns_are_cached_until_the_database_changes(league_db):
    breakdowns = DefenseBreakdowns(league_db)
    first = breakdowns.get()
    assert breakdowns.get() is first
    stat = os.stat(league_db)
    os.utime(league_db, (stat.st_atime, stat.st_mtime + 5))
    assert breakdowns.get() is not first


def test_missing_database_or_season(tmp_path, league_db):
    with pytest.raises(NoDefenseData):
        DefenseBreakdowns(None).get()
    with pytest.raises(NoDefenseData):
        DefenseBreakdowns(tmp_path / "missing.db").get()
    with pytest.raises(NoDefenseData):
        DefenseBreakdowns(league_db).get(2019)
    with pytest.raises(NoDefenseData):
        LeagueDefenses(sqlite3.connect(league_db), 2030)


# ---------- API ----------

def _use(league_db, matchups=None):
    app.dependency_overrides[get_defense_breakdowns] = lambda: DefenseBreakdowns(league_db)
    if matchups is not None:
        app.dependency_overrides[get_matchups] = lambda: (matchups, {"season": 2025, "weeks": [1, 8]})


def test_league_endpoint(league_db):
    _use(league_db)
    body = client.get("/defenses").json()
    assert body["season"] == 2025 and body["through_week"] == 6 and len(body["teams"]) == 4


def test_team_endpoint_with_upcoming_projections(league_db):
    schedule = {
        "BUF": {6: Game(6, "NE", True, played=True), 7: Game(7, "MIA", False, played=False),
                8: Game(8, "NYJ", True, played=False)},
        "MIA": {7: Game(7, "BUF", True, played=False)},
    }
    models = TeamModels(dst={
        "BUF": {7: ModelEstimate(8.5, (("opponent's Vegas total", 1.2),)), 8: ModelEstimate(6.0)},
        "MIA": {7: ModelEstimate(9.0)},
    })
    _use(league_db, MatchupData(season=2025, schedule=schedule, team_models=models))
    resp = client.get("/defenses/buf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["team"] == "BUF"
    assert [g["key"] for g in body["groups"]] == ["DL", "LB", "CB", "S"]
    assert [u["key"] for u in body["units"]] == ["pass_rush", "coverage", "run_defense", "scoring"]
    upcoming = body["upcoming"]
    assert [g["week"] for g in upcoming] == [7, 8]
    assert (upcoming[0]["projection"], upcoming[0]["rank"], upcoming[0]["teams"]) == (8.5, 2, 2)
    assert upcoming[0]["factors"] == [["opponent's Vegas total", 1.2]]


def test_upcoming_is_empty_without_this_seasons_schedule(league_db):
    _use(league_db)
    assert client.get("/defenses/BUF").json()["upcoming"] == []


def test_errors(league_db):
    assert client.get("/defenses").json()["code"] == "no_defense_data"
    _use(league_db)
    assert client.get("/defenses/KC").json()["code"] == "team_not_found"
    assert client.get("/defenses/B1F").status_code == 422
    assert client.get("/defenses", params={"season": 1900}).status_code == 422
    assert client.get("/defenses", params={"season": 2019}).json()["code"] == "no_defense_data"
