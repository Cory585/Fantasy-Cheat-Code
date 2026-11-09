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
