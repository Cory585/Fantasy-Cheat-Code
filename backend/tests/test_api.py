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


def test_player_repository_keeps_old_data_if_cache_is_corrupt(tmp_path):
    from app.api.dependencies import ReloadingPlayerData

    path = tmp_path / "players.json"
    data = ReloadingPlayerData(path)
    sample = data.current()[0]
    path.write_text("{not json", encoding="utf-8")
    assert data.current()[0] is sample


def test_meta_reports_data_source():
    data = client.get("/meta").json()
    assert data["players"] == 12  # sample data in tests
    assert data["source"] in {"sample", "synced"}


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
        {"side_a": {"player_ids": ["rb1"]}, "side_b": {"player_ids": ["wr1"]}, "league": {"roster_slots": {"SUPERFLEX": 1}}},
    ],
)
def test_analyze_rejects_out_of_range_input(body):
    assert client.post("/trades/analyze", json=body).status_code == 422


def test_duplicate_check_is_fast_at_the_limit():
    import time

    t = time.perf_counter()
    resp = client.post("/trades/analyze", json={"side_a": {"player_ids": ["rb1"] * 40}, "side_b": {"player_ids": ["wr1"]}})
    assert resp.status_code == 422 and time.perf_counter() - t < 1


def test_deeply_nested_json_is_a_client_error():
    body = ("[" * 5000 + "]" * 5000).encode()
    resp = client.post("/trades/analyze", content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 400


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
    repo, outlooks = ReloadingPlayerData(cache, database).current()
    assert repo.get("x1") is not None
    assert outlooks == {}


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
