import pytest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_list_players_by_position():
    players = client.get("/players", params={"position": "RB"}).json()
    assert players and all(p["position"] == "RB" for p in players)


def test_get_unknown_player_404():
    assert client.get("/players/nope").status_code == 404


def test_analyze_trade():
    resp = client.post(
        "/trades/analyze",
        json={"side_a": {"player_ids": ["te2"]}, "side_b": {"player_ids": ["rb1"]}},
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "favors_a"


def test_analyze_trade_unknown_player_404():
    resp = client.post(
        "/trades/analyze",
        json={"side_a": {"player_ids": ["nope"]}, "side_b": {"player_ids": ["rb1"]}},
    )
    assert resp.status_code == 404


def test_frontend_served_at_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Trade Analyzer" in resp.text


def test_analyze_trade_duplicate_player_on_one_side_422():
    resp = client.post(
        "/trades/analyze",
        json={"side_a": {"player_ids": ["rb1", "rb1"]}, "side_b": {"player_ids": ["wr1"]}},
    )
    assert resp.status_code == 422


def test_analyze_trade_rejects_invalid_league_size():
    # A negative league size used to index the position pool from the end (wrong baseline).
    for league in ({"num_teams": -1}, {"num_teams": 0}, {"roster_slots": {"QB": -1}}):
        resp = client.post(
            "/trades/analyze",
            json={"side_a": {"player_ids": ["qb1"]}, "side_b": {"player_ids": ["qb2"]}, "league": league},
        )
        assert resp.status_code == 422, league


@pytest.mark.parametrize(
    "path, heading",
    [("/tiers/", "Player Tier Lists"), ("/start-sit/", "Start/Sit Guide"), ("/draft/", "Draft Masterclass")],
)
def test_feature_pages_served(path, heading):
    resp = client.get(path)
    assert resp.status_code == 200
    assert heading in resp.text
    assert 'aria-current="page"' in resp.text


def test_shared_assets_served():
    for asset in ("/styles.css", "/theme.js", "/app.js"):
        assert client.get(asset).status_code == 200


def test_player_repository_reloads_when_cache_changes(tmp_path):
    from app.api.dependencies import ReloadingPlayerData
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    path = tmp_path / "players.json"
    data = ReloadingPlayerData(path)
    assert data.current()[0].get("rb1") is not None  # no cache yet -> sample data

    one = Player(id="x1", name="First", position=Position.QB, team="KC", ros_projection=100.0)
    write_cache(path, [one], 2026, range(10, 18), ScoringFormat.PPR)
    assert data.current()[0].get("x1").name == "First"

    two = Player(id="x2", name="Second", position=Position.QB, team="KC", ros_projection=90.0)
    write_cache(path, [two], 2026, range(10, 18), ScoringFormat.PPR)
    import os
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))  # filesystems with coarse mtimes
    assert data.current()[0].get("x2") is not None
    assert data.current()[0].get("x1") is None


def test_rest_of_season_projections_drop_played_weeks_without_a_resync(tmp_path, monkeypatch):
    from datetime import date

    from app.api.dependencies import ReloadingPlayerData
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    path = tmp_path / "players.json"
    weekly = {10: {"sleeper": 10.0}, 11: {"sleeper": 20.0}, 12: {"sleeper": 30.0}}
    write_cache(path, [Player(id="x1", name="A", position=Position.WR, team="KC", ros_projection=60.0,
                              projection_sources={"sleeper": 60.0}, weekly_projections=weekly)],
                2026, range(10, 19), ScoringFormat.PPR)
    data = ReloadingPlayerData(path)
    today = date(2026, 11, 11)  # week 10
    monkeypatch.setattr("app.services.season.today", lambda: today)
    assert data.current()[0].get("x1").ros_projection == 60.0
    today = date(2026, 11, 18)  # week 11: week 10 no longer counts
    player = data.current()[0].get("x1")
    assert player.ros_projection == 50.0 and player.projection_sources == {"sleeper": 50.0}
    assert player.weekly_projections == weekly
    today = date(2027, 2, 1)  # season over: keep the synced totals instead of dropping everyone to 0
    assert data.current()[0].get("x1").ros_projection == 60.0


def test_week_state_is_worked_out_once_a_day(tmp_path, monkeypatch):
    from datetime import date

    from app.api.dependencies import ReloadingPlayerData
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat
    from app.services import season

    path = tmp_path / "players.json"
    write_cache(path, [Player(id="x1", name="A", position=Position.WR, team="KC", ros_projection=1.0)],
                2026, range(10, 19), ScoringFormat.PPR)
    calls = []
    real = season.week_state
    monkeypatch.setattr(season, "week_state", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    day = date(2026, 11, 11)
    monkeypatch.setattr(season, "today", lambda: day)
    data = ReloadingPlayerData(path)
    for _ in range(5):
        data.current()
        data.matchups()
    assert len(calls) == 1
    day = date(2026, 11, 12)
    data.current()
    assert len(calls) == 2


def test_players_and_outlooks_are_read_together_once_per_request(monkeypatch):
    from app.api import dependencies

    calls = []
    monkeypatch.setattr(dependencies._data, "current", lambda: (calls.append(1), ("repo", {"o": 1}))[1])
    app.dependency_overrides.pop(dependencies.get_player_repository, None)
    app.dependency_overrides.pop(dependencies.get_outlooks, None)

    from fastapi import Depends, FastAPI

    probe = FastAPI()

    @probe.get("/probe")
    def both(repo=Depends(dependencies.get_player_repository), outlooks=Depends(dependencies.get_outlooks)):
        return {"repo": repo, "outlooks": outlooks}

    assert TestClient(probe).get("/probe").json() == {"repo": "repo", "outlooks": {"o": 1}}
    assert len(calls) == 1


def test_projections_move_with_the_weeks_while_a_broken_cache_is_ignored(tmp_path, monkeypatch):
    import os
    from datetime import date

    from app.api.dependencies import ReloadingPlayerData
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    path = tmp_path / "players.json"
    weekly = {10: {"sleeper": 10.0}, 11: {"sleeper": 20.0}}
    write_cache(path, [Player(id="x1", name="A", position=Position.WR, team="KC", ros_projection=30.0,
                              projection_sources={"sleeper": 30.0}, weekly_projections=weekly)],
                2026, range(10, 19), ScoringFormat.PPR)
    today = date(2026, 11, 11)  # week 10
    monkeypatch.setattr("app.services.season.today", lambda: today)
    data = ReloadingPlayerData(path)
    assert data.current()[0].get("x1").ros_projection == 30.0
    path.write_text("{not json", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    today = date(2026, 11, 18)  # week 11: the old players are still served, without week 10
    assert data.current()[0].get("x1").ros_projection == 20.0


def test_player_repository_keeps_old_data_if_cache_is_corrupt(tmp_path):
    from app.api.dependencies import ReloadingPlayerData

    path = tmp_path / "players.json"
    data = ReloadingPlayerData(path)
    sample = data.current()[0]
    path.write_text("{not json", encoding="utf-8")
    assert data.current()[0] is sample


def test_a_broken_cache_is_read_once_not_on_every_request(tmp_path, monkeypatch):
    from app.api import dependencies

    path = tmp_path / "players.json"
    data = dependencies.ReloadingPlayerData(path)
    data.current()
    path.write_text("{not json", encoding="utf-8")
    reads = []
    real = dependencies.load_player_cache
    monkeypatch.setattr(dependencies, "load_player_cache", lambda p: (reads.append(1), real(p))[1])
    for _ in range(3):
        data.current()
    assert len(reads) == 1


def _bump(path, seconds=5):
    import os

    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + seconds))  # filesystems with coarse mtimes


def _collected_data(tmp_path, monkeypatch):
    """A cache and a collected database, loaded once, with player cache reads counted."""
    from app.api import dependencies
    from app.data import db
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    cache, database = tmp_path / "players.json", tmp_path / "fantasy.db"
    write_cache(cache, [Player(id="x1", name="A", position=Position.QB, team="KC", ros_projection=100.0)],
                2026, range(10, 18), ScoringFormat.PPR)
    conn = db.connect(database)
    db.record_completed_run(conn, 0)
    data = dependencies.ReloadingPlayerData(cache, database)
    data.current()
    reads = []
    real = dependencies.load_player_cache
    monkeypatch.setattr(dependencies, "load_player_cache", lambda p: (reads.append(1), real(p))[1])
    return data, conn, database, reads


TEAM = {"abbrev": "DET", "espn_id": 8, "location": None, "name": None, "display_name": "Detroit Lions",
        "color": None, "logo_url": None}


def test_a_running_collection_does_not_reload_the_data_until_it_finishes(tmp_path, monkeypatch):
    from app.data import db

    data, conn, database, reads = _collected_data(tmp_path, monkeypatch)
    for espn_id in range(3):  # commits from a collection still in progress
        db.upsert(conn, "teams", [{**TEAM, "espn_id": espn_id}])
        _bump(database, 5 * (espn_id + 1))
        data.current()
    assert reads == []
    db.record_completed_run(conn, 3)
    _bump(database, 30)
    data.current()
    data.current()
    assert len(reads) == 1


def test_a_database_without_a_finished_collection_reloads_when_the_file_changes(tmp_path, monkeypatch):
    from app.data import db

    data, conn, database, reads = _collected_data(tmp_path, monkeypatch)
    conn.execute("DELETE FROM collection_runs")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _bump(database)
    data.current()
    data.current()
    assert len(reads) == 1
    assert db.last_completed_run(conn) is None


def test_requests_are_served_the_current_data_while_new_data_is_built(tmp_path, monkeypatch):
    import threading

    from app.api import dependencies

    data, conn, database, _ = _collected_data(tmp_path, monkeypatch)
    old = data.current()[0]
    started, release = threading.Event(), threading.Event()
    real = dependencies.load_player_cache

    def slow_load(path):
        started.set()
        release.wait(10)
        return real(path)

    monkeypatch.setattr(dependencies, "load_player_cache", slow_load)
    _bump(tmp_path / "players.json")
    builder = threading.Thread(target=data.current)
    builder.start()
    try:
        assert started.wait(10)
        assert data.current()[0] is old  # doesn't wait for the build
    finally:
        release.set()
        builder.join(10)
    assert data.current()[0] is not old


def test_a_file_deleted_while_checking_for_changes_is_treated_as_missing(tmp_path, monkeypatch):
    from pathlib import Path

    from app.api.dependencies import _mtime

    kept, wal = tmp_path / "fantasy.db", tmp_path / "fantasy.db-wal"
    kept.write_bytes(b"x")
    wal.write_bytes(b"x")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == wal:  # SQLite removed the -wal file after it was seen to exist
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert _mtime(kept, wal) == real_stat(kept).st_mtime
    assert _mtime(wal) is None


def test_meta_reports_data_source():
    data = client.get("/meta").json()
    assert data["players"] == 12  # sample data in tests
    assert data["source"] in {"sample", "synced"}
    assert data["current_week"] == data["weeks"][0] == 1  # sample data: no schedule, weeks 1-18
    assert data["matchups"] is False
    assert data["source"] == "sample" and data["stale"] is False


def test_meta_reports_synced_data_and_whether_it_is_stale(monkeypatch):
    from datetime import date

    from app.api.dependencies import get_data_source, get_matchups
    from app.services.matchups import MatchupData

    cache = {"season": 2026, "weeks": [10, 18], "scoring": "ppr", "synced_at": "2026-11-11T09:00:00+00:00"}
    app.dependency_overrides[get_data_source] = lambda: "synced"
    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), cache)
    monkeypatch.setattr("app.services.season.today", lambda: date(2026, 11, 12))
    data = client.get("/meta").json()
    assert (data["source"], data["season"], data["scoring"]) == ("synced", 2026, "ppr")
    assert (data["current_week"], data["synced_week"], data["stale"]) == (10, 10, False)
    monkeypatch.setattr("app.services.season.today", lambda: date(2026, 11, 18))
    data = client.get("/meta").json()
    assert (data["current_week"], data["synced_week"], data["stale"]) == (11, 10, True)
    # A sync run before the cached weeks (--start-week 12 in week 10) counts as their first week.
    cache.update(weeks=[12, 18])
    monkeypatch.setattr("app.services.season.today", lambda: date(2026, 11, 12))
    data = client.get("/meta").json()
    assert (data["current_week"], data["synced_week"], data["stale"]) == (12, 12, False)


def test_unreadable_cache_reports_sample_source(tmp_path):
    from app.api.dependencies import ReloadingPlayerData

    cache = tmp_path / "players.json"
    cache.write_text('{"season": 2026, "players": [{"id": "x1", "name": "No position"}]}', encoding="utf-8")
    assert ReloadingPlayerData(cache).source() == "sample"  # the file exists, but sample data is served


@pytest.mark.parametrize(
    "body",
    [
        {"side_a": {"player_ids": ["rb1"] * 41}, "side_b": {"player_ids": ["wr1"]}},  # too many players
        {"side_a": {"player_ids": ["rb1"], "team_name": "A" * 51}, "side_b": {"player_ids": ["wr1"]}},
        {"side_a": {"player_ids": ["rb1"], "roster_ids": ["x"] * 101}, "side_b": {"player_ids": ["wr1"]}},
        {"side_a": {"player_ids": ["x" * 65]}, "side_b": {"player_ids": ["wr1"]}},  # absurd id
        {"side_a": {"player_ids": [""]}, "side_b": {"player_ids": ["wr1"]}},
        {"side_a": {"player_ids": ["rb1"]}, "side_b": {"player_ids": ["wr1"]}, "league": {"num_teams": 33}},
        {"side_a": {"player_ids": ["rb1"]}, "side_b": {"player_ids": ["wr1"]}, "league": {"roster_slots": {"FLEX": 11}}},
        {"side_a": {"player_ids": ["rb1"]}, "side_b": {"player_ids": ["wr1"]}, "league": {"roster_slots": {"BENCH": 1}}},
    ],
)
def test_analyze_rejects_out_of_range_input(body):
    assert client.post("/trades/analyze", json=body).status_code == 422


def test_analyze_values_qbs_higher_in_superflex():
    body = {"side_a": {"player_ids": ["qb2"]}, "side_b": {"player_ids": ["rb2"]},
            "league": {"num_teams": 1, "roster_slots": {"QB": 1, "RB": 1}}}
    one_qb = client.post("/trades/analyze", json=body).json()
    body["league"]["roster_slots"]["SUPERFLEX"] = 1
    superflex = client.post("/trades/analyze", json=body).json()
    assert superflex["side_a"]["total_value_given"] > one_qb["side_a"]["total_value_given"]


def test_analyze_rejects_scoring_the_synced_data_was_not_built_for():
    from app.api.dependencies import get_matchups
    from app.services.matchups import MatchupData

    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), {"season": 2026, "scoring": "ppr"})
    body = {"side_a": {"player_ids": ["rb1"]}, "side_b": {"player_ids": ["wr1"]}}
    assert client.post("/trades/analyze", json=body).status_code == 200
    assert client.post("/trades/analyze", json={**body, "league": {"scoring": "ppr"}}).status_code == 200
    resp = client.post("/trades/analyze", json={**body, "league": {"scoring": "standard"}})
    assert resp.status_code == 422 and resp.json()["code"] == "scoring_unavailable"


def test_duplicate_check_is_fast_at_the_limit():
    import time

    t = time.perf_counter()
    resp = client.post("/trades/analyze", json={"side_a": {"player_ids": ["rb1"] * 40}, "side_b": {"player_ids": ["wr1"]}})
    assert resp.status_code == 422 and time.perf_counter() - t < 1


def test_deeply_nested_json_is_a_client_error():
    body = ("[" * 5000 + "]" * 5000).encode()
    resp = client.post("/trades/analyze", content=body, headers={"content-type": "application/json"})
    # Rejected as invalid without echoing the input back (echoing it used to overflow the stack).
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_request"
    assert "[[[[" not in resp.text


def test_oversized_body_is_rejected():
    body = b'{"side_a": {"player_ids": ["rb1"]}, "junk": "' + b"x" * 300_000 + b'"}'
    resp = client.post("/trades/analyze", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_corrupt_database_falls_back_to_projections_only(tmp_path):
    from app.api.dependencies import ReloadingPlayerData
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    cache, database = tmp_path / "players.json", tmp_path / "fantasy.db"
    write_cache(cache, [Player(id="x1", name="A", position=Position.QB, team="KC", ros_projection=100.0)],
                2026, range(10, 18), ScoringFormat.PPR)
    database.write_bytes(b"this is not a sqlite database" * 100)
    data = ReloadingPlayerData(cache, database)
    repo, outlooks = data.current()
    assert repo.get("x1") is not None
    assert outlooks == {}
    matchups, meta = data.matchups()
    assert not matchups.available and meta["weeks"] == [10, 17]


def test_start_sit_reads_matchups_and_weeks_from_collected_data(tmp_path):
    from app.api.dependencies import ReloadingPlayerData, get_start_sit_advisor
    from app.data import db
    from app.data.sync import write_cache
    from app.models.player import Player, Position
    from app.models.trade import ScoringFormat

    cache, database = tmp_path / "players.json", tmp_path / "fantasy.db"
    write_cache(cache, [Player(id="x1", name="A", position=Position.QB, team="KC", ros_projection=100.0)],
                2026, range(10, 18), ScoringFormat.PPR)
    conn = db.connect(database)
    db.upsert(conn, "games", [{"game_id": "g", "season": 2026, "game_type": "REG", "week": 11, "gameday": None,
                               "gametime": None, "away_team": "KC", "home_team": "DEN", "away_score": None,
                               "home_score": None, "spread_line": -2.5, "total_line": 44.5, "roof": None,
                               "surface": None, "temp": None, "wind": None, "div_game": None, "espn_id": None}])
    conn.close()
    advisor = get_start_sit_advisor(ReloadingPlayerData(cache, database).matchups())
    assert advisor.weeks == (10, 17) and advisor.season == 2026
    assert advisor.matchups.game("KC", 11).implied_total == 23.5


def test_cache_with_invalid_player_keeps_serving(tmp_path):
    from app.api.dependencies import ReloadingPlayerData

    cache = tmp_path / "players.json"
    cache.write_text('{"season": 2026, "players": [{"id": "x1", "name": "No position"}]}', encoding="utf-8")
    repo, _ = ReloadingPlayerData(cache).current()
    assert repo.get("rb1") is not None  # falls back to sample data instead of crashing


def test_draft_page_has_slot_dropdown_and_script():
    page = client.get("/draft/").text
    assert 'id="draft-slot"' in page and "/draft/draft.js" in page
    assert client.get("/draft/draft.js").status_code == 200


def test_draft_page_loads_its_script_once():
    assert client.get("/draft/").text.count('src="/draft/draft.js"') == 1
