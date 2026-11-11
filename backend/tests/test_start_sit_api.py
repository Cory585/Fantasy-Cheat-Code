from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_compare_two_sample_players():
    resp = client.get("/start-sit", params=[("player", "wr1"), ("player", "wr3"), ("week", 3)])
    assert resp.status_code == 200
    body = resp.json()
    assert body["week"] == 3 and body["slot"] == "WR"
    assert body["recommendation"]["start"] == ["wr1"]
    assert body["recommendation"]["confidence"] in {"confident", "lean", "toss_up"}
    assert [p["player"]["id"] for p in body["players"]] == ["wr1", "wr3"]
    assert body["players"][0]["floor"] <= body["players"][0]["if_active"] <= body["players"][0]["ceiling"]


def test_defaults_to_current_week_and_infers_flex():
    body = client.get("/start-sit", params=[("player", "rb1"), ("player", "wr1")]).json()
    assert body["week"] == 1 and body["slot"] == "FLEX"


def test_multiple_starts():
    params = [("player", p) for p in ("wr1", "wr2", "wr3")] + [("starts", 2)]
    body = client.get("/start-sit", params=params).json()
    assert len(body["recommendation"]["start"]) == 2 and len(body["recommendation"]["sit"]) == 1


def test_errors():
    def get(params):
        return client.get("/start-sit", params=params)

    assert get([("player", "wr1")]).status_code == 422  # need at least two
    assert get([("player", "wr1"), ("player", "nobody")]).json()["code"] == "player_not_found"
    resp = get([("player", "wr1"), ("player", "k1")])
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_start_sit"
    assert get([("player", "wr1"), ("player", "rb1"), ("slot", "WR")]).json()["code"] == "invalid_start_sit"
    assert get([("player", "wr1"), ("player", "wr1")]).json()["code"] == "invalid_start_sit"
    assert get([("player", "wr1"), ("player", "wr2"), ("starts", 2)]).json()["code"] == "invalid_start_sit"
    assert get([("player", "wr1"), ("player", "wr2"), ("week", 18)]).json()["code"] == "invalid_week"
    assert get([("player", "wr1"), ("player", "<script>")]).json()["code"] == "invalid_request"
    assert get([("player", p) for p in ["wr1"] * 9]).status_code == 422
