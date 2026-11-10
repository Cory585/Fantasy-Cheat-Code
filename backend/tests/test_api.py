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
