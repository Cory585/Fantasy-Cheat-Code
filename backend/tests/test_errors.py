"""Every error response has {"detail", "code"}, and internals never leak."""

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_player_repository
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_unknown_player_has_specific_code():
    resp = client.get("/players/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == "player_not_found"


def test_trade_with_unknown_player_has_specific_code():
    resp = client.post("/trades/analyze", json={"side_a": {"player_ids": ["nope"]}, "side_b": {"player_ids": ["rb1"]}})
    assert resp.json()["code"] == "player_not_found"


def test_validation_errors_keep_field_list_without_echoing_input():
    resp = client.get("/draft/plan", params={"slot": "not-a-number-SECRET"})
    body = resp.json()
    assert resp.status_code == 422 and body["code"] == "invalid_request"
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"]
    assert "SECRET" not in resp.text


def test_bad_draft_slot_code():
    assert client.get("/draft/plan", params={"teams": 8, "slot": 9}).json()["code"] == "invalid_draft_slot"


@pytest.mark.parametrize("player_id", ["a" * 33, "bad id", "..%2F..%2Fetc"])
def test_player_ids_outside_the_allowed_pattern_are_rejected(player_id):
    assert client.get(f"/players/{player_id}").status_code in (404, 422)


def test_method_not_allowed_has_code():
    resp = client.delete("/draft/plan")
    assert resp.status_code == 405 and resp.json()["code"] == "method_not_allowed"


def test_unexpected_errors_are_generic_500s():
    def explode():
        raise RuntimeError("database password is hunter2")

    app.dependency_overrides[get_player_repository] = explode
    resp = client.get("/players")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    assert "hunter2" not in resp.text
