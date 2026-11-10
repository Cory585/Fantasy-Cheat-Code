import threading
import time

import httpx
import pytest

from app.data.profile_cache import CachedProfile, ProfileCache
from app.data.sources.espn_athletes import AthleteNotFound
from app.services.profiles import ProfileService, ProfileUnavailable


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class FakeSource:
    def __init__(self):
        self.calls = []
        self.fail = None  # exception to raise from bio()
        self.missing = set()  # ids ESPN doesn't know
        self.gate = None  # threading.Event to hold a fetch open

    def bio(self, espn_id):
        self.calls.append(("bio", espn_id))
        if self.gate:
            self.gate.wait(2)
        if self.fail:
            raise self.fail
        if espn_id in self.missing:
            raise AthleteNotFound(str(espn_id))
        return {"name": f"Player {espn_id}"}

    def stats(self, espn_id, league):
        self.calls.append((league, espn_id))
        if league == "college":
            raise AthleteNotFound("no college record")
        return [{"label": "Rushing"}]


@pytest.fixture
def setup(tmp_path):
    source, clock = FakeSource(), Clock()
    service = ProfileService(source, ProfileCache(tmp_path / "p.db"), ttl=100, not_found_ttl=1000,
                             failure_backoff=50, clock=clock)
    return service, source, clock


def test_fetches_once_then_serves_from_cache(setup):
    service, source, clock = setup
    first = service.get(1)
    assert first.freshness == "fresh"
    assert first.profile.bio == {"name": "Player 1"}
    assert first.profile.nfl_stats == [{"label": "Rushing"}] and first.profile.college_stats == []
    assert service.get(1).freshness == "cached"
    assert len(source.calls) == 3  # bio, nfl, college: no second fetch


def test_refetches_after_ttl(setup):
    service, source, clock = setup
    service.get(1)
    clock.now += 101
    assert service.get(1).freshness == "fresh"
    assert len(source.calls) == 6


def test_not_found_is_cached_longer(setup):
    service, source, clock = setup
    source.missing.add(2)
    assert service.get(2).profile.status == "not_found"
    clock.now += 500  # past ttl, within not_found_ttl
    assert service.get(2).freshness == "cached"
    assert source.calls == [("bio", 2)]


def test_failure_without_cache_raises_and_backs_off(setup):
    service, source, clock = setup
    source.fail = httpx.ConnectError("down")
    with pytest.raises(ProfileUnavailable) as info:
        service.get(3)
    assert info.value.reason == "upstream_error"
    calls = len(source.calls)
    with pytest.raises(ProfileUnavailable):
        service.get(3)  # within the backoff: ESPN isn't contacted again
    assert len(source.calls) == calls
    clock.now += 51
    source.fail = None
    assert service.get(3).freshness == "fresh"


def test_failure_with_cache_serves_stale(setup):
    service, source, clock = setup
    service.get(4)
    clock.now += 101
    source.fail = httpx.ReadTimeout("slow")
    result = service.get(4)
    assert result.freshness == "stale" and result.profile.bio == {"name": "Player 4"}


def _run_overlapping(service, source, espn_id, n=4):
    """Start n requests for one player and hold the first fetch open until the others
    are waiting on it, so the requests genuinely overlap."""
    source.gate = threading.Event()
    results, errors = [], []

    def call():
        try:
            results.append(service.get(espn_id))
        except ProfileUnavailable as error:
            errors.append(error)

    threads = [threading.Thread(target=call) for _ in range(n)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5
    while ("bio", espn_id) not in source.calls and time.monotonic() < deadline:
        time.sleep(0.005)
    time.sleep(0.2)  # let the other requests reach the wait
    source.gate.set()
    for t in threads:
        t.join(5)
    return results, errors


def test_concurrent_requests_for_one_player_share_a_fetch(setup):
    service, source, clock = setup
    results, errors = _run_overlapping(service, source, 5)
    assert len(results) == 4 and not errors
    assert source.calls.count(("bio", 5)) == 1
    assert all(r.profile.bio == {"name": "Player 5"} for r in results)


def test_concurrent_requests_share_a_failure_too(setup):
    service, source, clock = setup
    source.fail = httpx.ReadTimeout("slow")
    results, errors = _run_overlapping(service, source, 9)
    assert not results and len(errors) == 4
    assert source.calls.count(("bio", 9)) == 1  # waiters don't retry ESPN one after another
    assert {e.reason for e in errors} == {"upstream_error"}


def test_waiters_give_up_after_wait_timeout(tmp_path):
    source, clock = FakeSource(), Clock()
    service = ProfileService(source, ProfileCache(tmp_path / "p.db"), wait_timeout=0.05, clock=clock)
    source.gate = threading.Event()
    leader = threading.Thread(target=lambda: service.get(11))
    leader.start()
    while ("bio", 11) not in source.calls:
        time.sleep(0.005)
    with pytest.raises(ProfileUnavailable) as info:
        service.get(11)
    assert info.value.reason == "busy"
    source.gate.set()
    leader.join(5)


def test_busy_when_too_many_fetches(tmp_path):
    source, clock = FakeSource(), Clock()
    source.gate = threading.Event()
    service = ProfileService(source, ProfileCache(tmp_path / "p.db"), max_concurrent=1, clock=clock)
    holder = threading.Thread(target=lambda: service.get(6))
    holder.start()
    while ("bio", 6) not in source.calls:
        pass
    with pytest.raises(ProfileUnavailable) as info:
        service.get(7)
    assert info.value.reason == "busy"
    source.gate.set()
    holder.join(5)


def test_corrupt_cache_row_is_a_miss(tmp_path):
    cache = ProfileCache(tmp_path / "p.db")
    cache.put(CachedProfile(8, "ok", 0, {"a": 1}, [], []))
    import sqlite3

    conn = sqlite3.connect(tmp_path / "p.db")
    with conn:
        conn.execute("UPDATE player_profiles SET bio = '{not json' WHERE espn_id = 8")
    conn.close()
    assert cache.get(8) is None


def test_rejects_invalid_ids(setup):
    service, source, _ = setup
    with pytest.raises(ValueError):
        service.get(-1)
    assert source.calls == []


def test_deleted_cache_file_is_recreated(setup, tmp_path):
    service, source, clock = setup
    service.get(12)
    (tmp_path / "p.db").unlink()
    clock.now += 101  # expired, so it refetches and writes to a fresh file
    assert service.get(12).freshness == "fresh"
    assert ProfileCache(tmp_path / "p.db").get(12) is not None


def test_unusable_cache_does_not_fail_or_refetch_every_time(tmp_path):
    source, clock = FakeSource(), Clock()
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the cache folder should be")
    service = ProfileService(source, ProfileCache(blocked / "p.db"), clock=clock)
    assert service.get(13).freshness == "fresh"
    assert service.get(13).freshness == "cached"  # from memory, not ESPN
    assert source.calls.count(("bio", 13)) == 1
