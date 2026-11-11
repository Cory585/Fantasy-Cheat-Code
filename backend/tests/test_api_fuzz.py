"""Fuzz the HTTP API: no input should produce a 500, every error should have the documented
shape, and every response should carry the security headers."""

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.dependencies import get_local_player_data, get_player_repository, get_profile_service
from app.data.profile_cache import CachedProfile
from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.main import app, limiter
from app.models.player import Player, Position
from app.services.player_profile import LocalPlayerData
from app.services.profiles import ProfileResult, ProfileUnavailable

client = TestClient(app, raise_server_exceptions=False)
FUZZ = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

PLAYERS = SAMPLE_PLAYERS + [
    Player(id="9221", name="Linked Back", position=Position.RB, team="DET", espn_id=4429795),
    Player(id="xss", name="<img src=x onerror=alert(1)>", position=Position.WR, team="DET", espn_id=5),
]


class ChaosProfiles:
    """Profile service stand-in that returns every kind of outcome, including junk stats."""

    def __init__(self):
        self.n = 0

    def get(self, espn_id):
        self.n += 1
        kind = self.n % 4
        if kind == 0:
            raise ProfileUnavailable("busy", 5)
        if kind == 1:
            raise ProfileUnavailable("upstream_error", 600)
        if kind == 2:
            return ProfileResult(CachedProfile(espn_id, "not_found", 0, None, [], []), "cached")
        junk = [{"name": "x"}, "nope", {"label": "Rushing", "name": "rushing",
                                         "columns": [{"label": "YDS", "name": "Yards"}],
                                         "seasons": [{"year": 2025, "values": [1.0]}]}]
        return ProfileResult(CachedProfile(espn_id, "ok", 0, {"name": "Z", "age": "old", "height": 5}, junk, junk),
                             "stale")


@pytest.fixture(autouse=True)
def fuzz_environment():
    limiter.enabled = False
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(PLAYERS)
    app.dependency_overrides[get_profile_service] = ChaosProfiles
    app.dependency_overrides[get_local_player_data] = lambda: LocalPlayerData(None)
    yield
    limiter.enabled = True


def check(resp):
    assert resp.status_code < 500, (resp.status_code, resp.text[:300])
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    has_body = resp.request.method != "HEAD"  # HEAD responses carry headers only
    if has_body and resp.status_code >= 400 and resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        assert set(body) == {"detail", "code"}, body
        assert isinstance(body["code"], str) and body["code"]


text = st.text(max_size=40)
query_value = st.one_of(st.integers(-10**12, 10**12).map(str), text, st.just(""), st.floats().map(str))


@FUZZ
@given(params=st.dictionaries(st.sampled_from(["teams", "slot", "rounds", "targets", "x"]), query_value, max_size=5))
def test_draft_plan_never_500s(params):
    resp = client.get("/draft/plan", params=params)
    check(resp)
    if resp.status_code == 200:
        plan = resp.json()
        assert 4 <= plan["teams"] <= 20 and 1 <= plan["slot"] <= plan["teams"]


path_segment = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=50)


@FUZZ
@given(player_id=st.one_of(path_segment, st.sampled_from([p.id for p in PLAYERS])))
def test_player_and_profile_routes_never_500(player_id):
    from urllib.parse import quote

    for suffix in ("", "/profile"):
        resp = client.get(f"/players/{quote(player_id, safe='')}{suffix}")
        check(resp)


@FUZZ
@given(player_id=st.sampled_from([p.id for p in PLAYERS]))
def test_every_profile_outcome_is_a_valid_profile(player_id):
    resp = client.get(f"/players/{player_id}/profile")
    check(resp)
    body = resp.json()
    assert body["espn"]["status"] in {"fresh", "cached", "stale", "not_found", "unavailable", "not_applicable"}
    for category in body["nfl_stats"] + body["college_stats"]:
        assert category["label"]  # malformed cached categories were skipped, not served


json_values = st.recursive(
    # JSON has no infinity, so the client can't send it.
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=20),
    lambda children: st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=12), children, max_size=5),
    max_leaves=30,
)
trade_sides = st.fixed_dictionaries({"player_ids": st.lists(st.one_of(text, st.sampled_from([p.id for p in PLAYERS])),
                                                            max_size=50)})


@FUZZ
@given(body=st.one_of(
    json_values,
    st.fixed_dictionaries({"side_a": trade_sides, "side_b": trade_sides},
                          optional={"league": st.dictionaries(st.sampled_from(["scoring", "num_teams", "lineup"]),
                                                              json_values, max_size=3)}),
))
def test_trade_analysis_never_500s(body):
    check(client.post("/trades/analyze", json=body))


@FUZZ
@given(raw=st.binary(max_size=500), content_type=st.sampled_from(["application/json", "text/plain", "", "application/x-www-form-urlencoded"]))
def test_raw_bodies_never_500(raw, content_type):
    check(client.post("/trades/analyze", content=raw, headers={"content-type": content_type}))


@FUZZ
@given(
    path=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.%~", min_size=1, max_size=12), min_size=1, max_size=4),
    # Printable ASCII: HTTP clients can't send other header bytes (raw bytes are covered in test_properties).
    accept=st.one_of(st.just("text/html"), st.just("application/json"), st.just("*/*"),
                     st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40)),
    method=st.sampled_from(["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
)
def test_arbitrary_paths_and_methods_never_500(path, accept, method):
    resp = client.request(method, "/" + "/".join(path), headers={"accept": accept})
    check(resp)


def test_player_names_are_returned_as_data_not_markup():
    # The API returns names verbatim as JSON strings; the pages insert them as text.
    resp = client.get("/players/xss")
    assert resp.json()["name"] == "<img src=x onerror=alert(1)>"
    assert resp.headers["content-type"].startswith("application/json")
