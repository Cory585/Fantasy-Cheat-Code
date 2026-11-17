import secrets

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_league_store
from app.data.league_store import LeagueStore, LeagueStoreError, owner_for
from app.main import app, limiter
from app.models.league import DEFAULT_SLOTS, MAX_LEAGUES

client = TestClient(app)


def new_key() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture
def key():
    return new_key()


@pytest.fixture
def no_rate_limits():
    limiter.enabled = False
    yield
    limiter.enabled = True


def headers(key):
    return {"X-Profile-Key": key}


def create(key, name="Work league", **body):
    return client.post("/leagues", json={"name": name, **body}, headers=headers(key))


def test_create_list_update_delete(key):
    assert client.get("/leagues", headers=headers(key)).json() == {"leagues": [], "max_leagues": MAX_LEAGUES}

    resp = create(key, "  Work   league ", slots={"SUPERFLEX": 1}, roster=["qb1", "rb1", "qb1"])
    assert resp.status_code == 201
    league = resp.json()
    assert league["name"] == "Work league"
    assert league["slots"] == {**DEFAULT_SLOTS, "SUPERFLEX": 1}
    assert league["roster"] == ["qb1", "rb1"]  # duplicates dropped, order kept
    assert league["created"].endswith("Z")

    saved = client.put(f"/leagues/{league['id']}", json={"name": "Home", "slots": {"QB": 2}, "roster": ["wr1"]},
                       headers=headers(key))
    assert saved.status_code == 200
    assert saved.json()["name"] == "Home" and saved.json()["roster"] == ["wr1"]
    assert saved.json()["slots"]["QB"] == 2 and saved.json()["slots"]["RB"] == DEFAULT_SLOTS["RB"]
    assert client.get(f"/leagues/{league['id']}", headers=headers(key)).json() == saved.json()

    assert client.delete(f"/leagues/{league['id']}", headers=headers(key)).status_code == 204
    assert client.get("/leagues", headers=headers(key)).json()["leagues"] == []
    assert client.delete(f"/leagues/{league['id']}", headers=headers(key)).json()["code"] == "league_not_found"


def test_leagues_are_listed_oldest_first(key):
    names = ["A", "B", "C"]
    for name in names:
        create(key, name)
    assert [lg["name"] for lg in client.get("/leagues", headers=headers(key)).json()["leagues"]] == names


def test_profile_key_is_required():
    for bad in (None, "", "short", "has spaces in it but is long enough to pass length", "x" * 129):
        resp = client.get("/leagues", headers={"X-Profile-Key": bad} if bad is not None else {})
        assert resp.status_code == 401, bad
        assert resp.json()["code"] == "profile_key_required"


def test_profiles_cannot_see_or_change_each_others_leagues(key):
    league = create(key).json()
    other = new_key()
    assert client.get("/leagues", headers=headers(other)).json()["leagues"] == []
    assert client.get(f"/leagues/{league['id']}", headers=headers(other)).status_code == 404
    assert client.put(f"/leagues/{league['id']}", json={"name": "Mine now"}, headers=headers(other)).status_code == 404
    assert client.delete(f"/leagues/{league['id']}", headers=headers(other)).status_code == 404
    assert client.get(f"/leagues/{league['id']}", headers=headers(key)).json()["name"] == "Work league"


def test_cap_of_ten_leagues(key, no_rate_limits):
    ids = [create(key, f"League {i}").json()["id"] for i in range(MAX_LEAGUES)]
    full = create(key, "One too many")
    assert full.status_code == 409
    assert full.json()["code"] == "league_limit"
    assert len(client.get("/leagues", headers=headers(key)).json()["leagues"]) == MAX_LEAGUES
    # The cap is per profile, and frees up when one is removed.
    assert create(new_key()).status_code == 201
    client.delete(f"/leagues/{ids[0]}", headers=headers(key))
    assert create(key, "Back under").status_code == 201


@pytest.mark.parametrize("body", [
    {},
    {"name": ""},
    {"name": "   "},
    {"name": "x" * 41},
    {"name": "ok", "slots": {"QB": 6}},
    {"name": "ok", "slots": {"QB": -1}},
    {"name": "ok", "slots": {"KR": 1}},
    {"name": "ok", "roster": ["bad id!"]},
    {"name": "ok", "roster": [f"p{i}" for i in range(41)]},
    {"name": "ok", "owner": "someone else"},
])
def test_invalid_leagues_are_rejected(key, body):
    resp = client.post("/leagues", json=body, headers=headers(key))
    assert resp.status_code == 422, body


def test_bad_league_ids_are_rejected(key):
    assert client.get("/leagues/x", headers=headers(key)).status_code == 422
    assert client.get("/leagues/aaaaaaaaaaaa", headers=headers(key)).status_code == 404


def test_making_and_removing_leagues_is_rate_limited(key):
    burst = next(rule.burst for rule in limiter.rules if rule.name == "league-changes")
    league_ids = []
    for i in range(burst):
        resp = create(key, f"L{i}") if i < MAX_LEAGUES else client.delete(
            f"/leagues/{league_ids.pop()}", headers=headers(key))
        assert resp.status_code in (201, 204), i
        if resp.status_code == 201:
            league_ids.append(resp.json()["id"])
    blocked = client.delete(f"/leagues/{league_ids[0]}", headers=headers(key))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1
    # Reading and saving use other buckets, so they still work.
    assert client.get("/leagues", headers=headers(key)).status_code == 200
    assert client.put(f"/leagues/{league_ids[0]}", json={"name": "Still saves"}, headers=headers(key)).status_code == 200


def test_store_failure_is_a_503(key, tmp_path):
    class Broken(LeagueStore):
        def _run(self, work):
            raise LeagueStoreError("disk on fire")

    app.dependency_overrides[get_league_store] = lambda: Broken(tmp_path / "x.db")
    resp = client.get("/leagues", headers=headers(key))
    assert resp.status_code == 503
    assert resp.json()["code"] == "leagues_unavailable"
    assert "disk on fire" not in resp.text


def test_store_keeps_only_a_hash_of_the_key(tmp_path, key):
    store = LeagueStore(tmp_path / "l.db")
    store.create(owner_for(key), "Hashed", dict(DEFAULT_SLOTS), [], MAX_LEAGUES)
    assert key.encode() not in (tmp_path / "l.db").read_bytes()
    assert store.list(owner_for(key))[0].name == "Hashed"


def test_store_has_a_global_cap(tmp_path):
    from app.data.league_store import StoreFull

    store = LeagueStore(tmp_path / "l.db", max_total=2)
    store.create("a", "1", {}, [], MAX_LEAGUES)
    store.create("b", "2", {}, [], MAX_LEAGUES)
    with pytest.raises(StoreFull):
        store.create("c", "3", {}, [], MAX_LEAGUES)


def put(key, league_id, body, if_match=None):
    extra = {"If-Match": if_match} if if_match is not None else {}
    return client.put(f"/leagues/{league_id}", json=body, headers={**headers(key), **extra})


def test_saves_carry_a_version(key):
    made = create(key)
    assert made.json()["version"] == 1 and made.headers["ETag"] == '"1"'
    league_id = made.json()["id"]
    saved = put(key, league_id, {"name": "A"}, if_match='"1"')
    assert saved.status_code == 200 and saved.json()["version"] == 2 and saved.headers["ETag"] == '"2"'
    got = client.get(f"/leagues/{league_id}", headers=headers(key))
    assert got.json()["version"] == 2 and got.headers["ETag"] == '"2"'


def test_stale_save_is_a_conflict_with_the_current_league(key):
    league_id = create(key).json()["id"]
    assert put(key, league_id, {"name": "From tab one", "roster": ["rb1"]}, if_match='"1"').status_code == 200
    stale = put(key, league_id, {"name": "From tab two", "roster": ["wr1"]}, if_match='"1"')
    assert stale.status_code == 409 and stale.json()["code"] == "league_conflict"
    assert stale.json()["league"]["name"] == "From tab one" and stale.json()["league"]["version"] == 2
    assert stale.headers["ETag"] == '"2"'
    assert client.get(f"/leagues/{league_id}", headers=headers(key)).json()["roster"] == ["rb1"]  # not overwritten


def test_saves_without_if_match_still_replace_the_league(key):
    league_id = create(key).json()["id"]
    put(key, league_id, {"name": "One"})
    assert put(key, league_id, {"name": "Two"}).json()["version"] == 3
    assert put(key, league_id, {"name": "Any"}, if_match="*").status_code == 200
    assert put(key, league_id, {"name": "Weak"}, if_match='W/"4"').status_code == 200


@pytest.mark.parametrize("if_match", ['"abc"', "1.5", '"-1"', '"' + "9" * 20 + '"', '""'])
def test_malformed_if_match_is_rejected(key, if_match):
    league_id = create(key).json()["id"]
    resp = put(key, league_id, {"name": "A"}, if_match=if_match)
    assert resp.status_code == 400 and resp.json()["code"] == "invalid_if_match"


def test_conflict_check_is_scoped_to_the_profile(key):
    league_id = create(key).json()["id"]
    assert put(new_key(), league_id, {"name": "Theirs"}, if_match='"1"').status_code == 404


def test_store_adds_versions_to_an_older_database(tmp_path):
    import sqlite3

    path = tmp_path / "leagues.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE leagues (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, "
                 "slots TEXT NOT NULL, roster TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    conn.execute("INSERT INTO leagues VALUES ('old-league', 'me', 'Old', '{}', '[]', 0, 0)")
    conn.commit()
    conn.close()
    store = LeagueStore(path)
    assert store.get("me", "old-league").version == 1
    assert store.update("me", "old-league", "New", {}, [], expected_version=1).version == 2


@pytest.mark.parametrize("raw", ["\u00b2".encode("latin-1"), "1\u00b3".encode("latin-1")])
def test_non_ascii_digits_in_if_match_are_rejected(key, raw):
    # str.isdigit() accepts superscript digits, which int() can't parse.
    league_id = create(key).json()["id"]
    resp = client.put(f"/leagues/{league_id}", json={"name": "A"}, headers={**headers(key), "If-Match": raw})
    assert resp.status_code == 400 and resp.json()["code"] == "invalid_if_match"


def test_version_column_is_added_once_and_tolerates_a_race(tmp_path, monkeypatch):
    import sqlite3

    from app.data import league_store

    path = tmp_path / "leagues.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE leagues (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, "
                 "slots TEXT NOT NULL, roster TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    league_store.add_version_column(conn)
    league_store.add_version_column(conn)  # another process got there first: no error
    conn.close()

    calls = []
    real = league_store.add_version_column
    monkeypatch.setattr(league_store, "add_version_column", lambda c: (calls.append(1), real(c)))
    store = LeagueStore(path)
    for _ in range(3):
        store.list("me")
    assert len(calls) == 1  # once per store, not on every request
