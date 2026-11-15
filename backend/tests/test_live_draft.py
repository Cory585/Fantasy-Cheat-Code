import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_live_drafts, get_player_repository
from app.data.repository import InMemoryPlayerRepository
from app.data.sample_players import SAMPLE_PLAYERS
from app.data.sources.espn_draft import (
    EspnCredentials,
    EspnDraft,
    EspnDraftPick,
    EspnDraftTeam,
    LeagueNotFound,
    LeaguePrivate,
)
from app.main import app, limiter
from app.services.live_draft import LiveDrafts, TooManySessions, to_live_draft

S2 = "AEBx" * 10
SWID = "{ABCDEF12-3456-7890-ABCD-EF1234567890}"

# The sample pool with ESPN ids, so picks map to players.
POOL = [p.model_copy(update={"espn_id": 1000 + i}) for i, p in enumerate(SAMPLE_PLAYERS)]
ESPN_IDS = [p.espn_id for p in POOL]


def make_draft(n_picks=0, status=None, league_id="777"):
    picks = [EspnDraftPick(overall=i + 1, round=1, round_pick=i + 1, team_id=i % 4 + 1,
                           espn_player_id=ESPN_IDS[i] if i < len(ESPN_IDS) else 99_000 + i)
             for i in range(n_picks)]
    return EspnDraft(
        league_id=league_id, season=2026, name="Office League",
        status=status or ("in_progress" if n_picks else "pre_draft"), draft_type="SNAKE",
        team_count=4, rounds=2,
        teams=[EspnDraftTeam(id=i, name=f"Team {i}", abbrev=f"T{i}", slot=i) for i in range(1, 5)],
        picks=picks,
    )


class FakeSource:
    """Returns the drafts in order (repeating the last), or raises one if it's an exception."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def draft(self, season, league_id, credentials=None):
        self.calls.append((season, league_id, credentials))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------- Sessions ----------

def test_create_reads_the_draft_once_and_keeps_the_session():
    source = FakeSource(make_draft())
    drafts = LiveDrafts(source)
    creds = EspnCredentials(espn_s2=S2, swid=SWID)
    session, draft = drafts.create("777", 2026, creds)
    assert draft.status == "pre_draft"
    assert source.calls == [(2026, "777", creds)]
    assert drafts.get(session.id) is session
    assert len(session.id) == 32
    assert "espn_s2" not in repr(session) and S2 not in repr(session)


def test_create_fails_without_keeping_a_session():
    drafts = LiveDrafts(FakeSource(LeaguePrivate("777")))
    with pytest.raises(LeaguePrivate):
        drafts.create("777", 2026, None)
    assert len(drafts) == 0


def test_snapshot_reads_espn_at_most_once_per_interval():
    clock = Clock()
    source = FakeSource(make_draft(), make_draft(1), make_draft(2))
    drafts = LiveDrafts(source, poll_seconds=4, clock=clock)
    session, _ = drafts.create("777", 2026, None)
    assert len(drafts.snapshot(session).picks) == 0  # still fresh from create
    clock.now += 3.9
    assert len(drafts.snapshot(session).picks) == 0
    clock.now += 0.1
    assert len(drafts.snapshot(session).picks) == 1
    assert len(source.calls) == 2


def test_unused_sessions_expire():
    clock = Clock()
    drafts = LiveDrafts(FakeSource(make_draft()), ttl_seconds=60, clock=clock)
    session, _ = drafts.create("777", 2026, None)
    clock.now += 59
    assert drafts.get(session.id) is session  # using it resets the clock
    clock.now += 59
    assert drafts.get(session.id) is session
    clock.now += 61
    assert drafts.get(session.id) is None


def test_session_cap_counts_only_live_sessions():
    clock = Clock()
    drafts = LiveDrafts(FakeSource(make_draft()), ttl_seconds=60, max_sessions=1, clock=clock)
    drafts.create("777", 2026, None)
    with pytest.raises(TooManySessions):
        drafts.create("778", 2026, None)
    clock.now += 61
    drafts.create("778", 2026, None)  # the first one expired
    assert len(drafts) == 1


def test_remove():
    drafts = LiveDrafts(FakeSource(make_draft()))
    session, _ = drafts.create("777", 2026, None)
    assert drafts.remove(session.id)
    assert drafts.get(session.id) is None
    assert not drafts.remove(session.id)


def test_picks_are_matched_to_the_player_pool():
    live = to_live_draft(make_draft(len(ESPN_IDS) + 1), POOL)
    first = live.picks[0]
    player = next(p for p in POOL if p.espn_id == ESPN_IDS[0])
    assert (first.player_id, first.name, first.position, first.team) == (
        player.id, player.name, player.position, player.team)
    outside = live.picks[-1]
    assert outside.player_id is None and outside.name is None and outside.espn_player_id == 99_000 + len(ESPN_IDS)


# ---------- API ----------

client = TestClient(app)


@pytest.fixture
def use_source():
    def install(*results, **kwargs):
        source = FakeSource(*results)
        drafts = LiveDrafts(source, poll_seconds=0, **kwargs)
        app.dependency_overrides[get_live_drafts] = lambda: drafts
        app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(POOL)
        return source, drafts
    return install


def start(**body):
    return client.post("/draft/live", json={"league_id": "777", "season": 2026, **body})


def test_start_returns_a_session_and_the_draft(use_source):
    source, drafts = use_source(make_draft(2))
    resp = start(espn_s2=S2, swid=SWID.strip("{}").lower())
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["session_id"]) == 32
    draft = body["draft"]
    assert (draft["name"], draft["status"], draft["team_count"]) == ("Office League", "in_progress", 4)
    assert [p["overall"] for p in draft["picks"]] == [1, 2]
    assert draft["picks"][0]["player_id"] is not None
    assert source.calls[0][2] == EspnCredentials(espn_s2=S2, swid=SWID.strip("{}").lower())
    assert S2 not in resp.text


def test_start_defaults_the_season_to_this_year(use_source):
    from datetime import date

    source, _ = use_source(make_draft())
    assert client.post("/draft/live", json={"league_id": "777"}).status_code == 201
    assert source.calls[0][0] == date.today().year


@pytest.mark.parametrize("body", [
    {"league_id": "abc"},
    {"league_id": "1" * 13},
    {"league_id": "777", "espn_s2": S2},  # one cookie without the other
    {"league_id": "777", "swid": SWID},
    {"league_id": "777", "espn_s2": "short", "swid": SWID},
    {"league_id": "777", "espn_s2": S2 + "\r\nX-Evil: 1", "swid": SWID},
    {"league_id": "777", "espn_s2": S2, "swid": "not-a-guid"},
    {"league_id": "777", "season": 1999},
])
def test_start_rejects_bad_input_without_echoing_it(use_source, body):
    source, _ = use_source(make_draft())
    resp = client.post("/draft/live", json=body)
    assert resp.status_code == 422
    assert source.calls == []
    assert S2 not in resp.text


@pytest.mark.parametrize(("error", "status", "code"), [
    (LeaguePrivate("777"), 403, "espn_private_league"),
    (LeagueNotFound("777"), 404, "espn_league_not_found"),
    (httpx.ConnectError("down"), 502, "espn_unavailable"),
    (ValueError("bad json"), 502, "espn_unavailable"),
])
def test_start_reports_espn_errors(use_source, error, status, code):
    use_source(error)
    resp = start()
    assert (resp.status_code, resp.json()["code"]) == (status, code)


def test_start_when_full(use_source):
    use_source(make_draft(), max_sessions=1)
    assert start().status_code == 201
    resp = start()
    assert (resp.status_code, resp.json()["code"]) == (503, "live_draft_busy")


def test_start_is_rate_limited(use_source):
    use_source(make_draft())
    was, limiter.enabled = limiter.enabled, True
    try:
        statuses = [start().status_code for _ in range(8)]
    finally:
        limiter.enabled = was
    assert statuses[:6] == [201] * 6 and statuses[-1] == 429


def test_get_and_delete(use_source):
    use_source(make_draft(), make_draft(1))
    session_id = start().json()["session_id"]
    resp = client.get(f"/draft/live/{session_id}")
    assert resp.status_code == 200
    assert len(resp.json()["picks"]) == 1
    assert client.delete(f"/draft/live/{session_id}").status_code == 204
    resp = client.get(f"/draft/live/{session_id}")
    assert (resp.status_code, resp.json()["code"]) == (404, "live_draft_not_found")


def test_unknown_or_malformed_session_ids(use_source):
    use_source(make_draft())
    assert client.get("/draft/live/" + "a" * 32).status_code == 404
    assert client.get("/draft/live/short").status_code == 422
    assert client.get("/draft/live/" + "a" * 32 + "/events").status_code == 404


def read_events(session_id):
    events = []
    with client.stream("GET", f"/draft/live/{session_id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-store"
        name = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    return events


def test_event_stream_sends_each_change_until_the_draft_is_complete(use_source):
    use_source(make_draft(), make_draft(), make_draft(1), make_draft(1), make_draft(3),
               make_draft(3, status="complete"))
    session_id = start().json()["session_id"]  # uses the first result
    events = read_events(session_id)
    assert [name for name, _ in events] == ["draft"] * 4
    assert [(d["status"], len(d["picks"])) for _, d in events] == [
        ("pre_draft", 0), ("in_progress", 1), ("in_progress", 3), ("complete", 3)]


def test_event_stream_reports_a_blip_and_keeps_going(use_source):
    use_source(make_draft(), httpx.ReadTimeout("slow"), make_draft(1, status="complete"))
    session_id = start().json()["session_id"]
    events = read_events(session_id)
    assert [name for name, _ in events] == ["problem", "draft"]
    assert events[0][1]["code"] == "espn_unavailable"


def test_event_stream_stops_when_access_is_lost(use_source):
    use_source(make_draft(), LeaguePrivate("777"))
    session_id = start().json()["session_id"]
    events = read_events(session_id)
    assert events == [("problem", {"code": "espn_private_league", "detail": events[0][1]["detail"]})]


def test_is_active_does_not_extend_a_session():
    clock = Clock()
    drafts = LiveDrafts(FakeSource(make_draft()), ttl_seconds=60, clock=clock)
    session, _ = drafts.create("777", 2026, None)
    clock.now += 59
    assert drafts.is_active(session)
    clock.now += 2
    assert not drafts.is_active(session)
    drafts.remove(session.id)
    assert not drafts.is_active(session)


def test_event_stream_stops_once_the_session_is_removed(use_source):
    source, drafts = use_source(make_draft())
    session_id = start().json()["session_id"]
    real_snapshot = drafts.snapshot

    def snapshot_then_disconnect(session):
        draft = real_snapshot(session)
        drafts.remove(session.id)  # as if the page disconnected mid-stream
        return draft

    drafts.snapshot = snapshot_then_disconnect
    events = read_events(session_id)
    assert [name for name, _ in events] == ["draft", "problem"]
    assert events[1][1]["code"] == "live_draft_not_found"
