import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.data import refresh
from app.data.refresh import (
    DAILY_DATASETS,
    Refresher,
    cache_age_hours,
    cached_scoring,
    hours_since_refresh,
    read_state,
    refresh_once,
)
from app.data.sync import SyncError
from app.models.trade import ScoringFormat


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def write_cache(path, hours_ago, scoring="ppr"):
    path.write_text(json.dumps({"synced_at": ago(hours_ago), "scoring": scoring, "players": []}), encoding="utf-8")


class FakeJobs:
    """Stands in for sync and collect, recording what they were asked to do."""

    def __init__(self, monkeypatch, sync_error=None):
        self.synced = []
        self.collected = []
        self.sync_error = sync_error
        monkeypatch.setattr(refresh, "sync", self.sync)
        monkeypatch.setattr(refresh, "collect", self.collect)
        monkeypatch.setattr(refresh.db, "connect", lambda path: type("Conn", (), {"close": lambda self: None})())

    def sync(self, output, scoring, *args, **kwargs):
        self.synced.append(scoring)
        if self.sync_error:
            raise self.sync_error

    def collect(self, datasets, seasons, conn):
        self.collected.append(datasets)
        return {f"{d}:{seasons[0]}": 1 for d in datasets}


def test_cache_age(tmp_path):
    cache = tmp_path / "players.json"
    assert cache_age_hours(cache) is None
    write_cache(cache, 5)
    assert 4.9 < cache_age_hours(cache) < 5.1
    cache.write_text("not json", encoding="utf-8")
    assert cache_age_hours(cache) is None


@pytest.mark.parametrize(("hours_ago", "due_now"), [(None, True), (7, True), (2, False)])
def test_first_refresh_is_due_when_the_cache_is_old_or_missing(tmp_path, hours_ago, due_now):
    cache = tmp_path / "players.json"
    if hours_ago is not None:
        write_cache(cache, hours_ago)
    delay = Refresher(6, job=lambda: True, cache_path=cache).first_delay()
    assert (delay == 0) is due_now
    if not due_now:
        assert 3.9 * 3600 < delay < 4.1 * 3600


def test_a_recent_failed_refresh_also_counts(tmp_path, monkeypatch):
    # Past the last week the sync keeps failing, so synced_at never moves. A restart must not
    # refresh again at once just because the cache is old.
    cache = tmp_path / "players.json"
    write_cache(cache, 100)
    FakeJobs(monkeypatch, sync_error=SyncError("start week past end week"))
    assert refresh_once(cache, tmp_path / "fantasy.db") is False
    assert hours_since_refresh(cache) < 0.1
    assert Refresher(6, job=lambda: True, cache_path=cache).first_delay() > 5.9 * 3600


def test_keeps_the_cached_scoring_format(tmp_path, monkeypatch):
    cache = tmp_path / "players.json"
    write_cache(cache, 10, scoring="half_ppr")
    assert cached_scoring(cache) == ScoringFormat.HALF_PPR
    jobs = FakeJobs(monkeypatch)
    assert refresh_once(cache, tmp_path / "fantasy.db") is True
    assert jobs.synced == [ScoringFormat.HALF_PPR]


def test_heavy_datasets_are_collected_at_most_daily(tmp_path, monkeypatch):
    cache = tmp_path / "players.json"
    jobs = FakeJobs(monkeypatch)
    refresh_once(cache, tmp_path / "fantasy.db")
    refresh_once(cache, tmp_path / "fantasy.db")
    first, second = jobs.collected
    assert set(DAILY_DATASETS) <= set(first)
    assert not set(DAILY_DATASETS) & set(second) and "injuries" in second
    assert read_state(cache)["last_full_collect"]


def test_refresh_once_reports_failures_and_keeps_going(tmp_path, monkeypatch):
    jobs = FakeJobs(monkeypatch, sync_error=SyncError("no players"))
    assert refresh_once(tmp_path / "players.json", tmp_path / "fantasy.db") is False
    assert len(jobs.collected) == 1  # the collect still ran


def test_only_one_refresh_runs_at_a_time(tmp_path, monkeypatch):
    cache = tmp_path / "players.json"
    jobs = FakeJobs(monkeypatch)
    lock = tmp_path / "refresh.lock"
    lock.write_text("123", encoding="utf-8")  # another worker is refreshing
    assert refresh_once(cache, tmp_path / "fantasy.db") is False
    assert jobs.synced == [] and lock.exists()
    # A lock left by a run that died is taken over.
    old = time.time() - refresh.STALE_LOCK_SECONDS - 60
    os.utime(lock, (old, old))
    assert refresh_once(cache, tmp_path / "fantasy.db") is True
    assert not lock.exists()


def test_keeps_running_after_a_failure(tmp_path):
    calls = []
    second = threading.Event()

    def job():
        calls.append(1)
        if len(calls) >= 2:
            second.set()
        raise RuntimeError("provider down")

    refresher = Refresher(0.1 / 3600, job=job, cache_path=tmp_path / "missing.json")  # every 0.1 s
    refresher.start()
    assert second.wait(5)
    refresher.stop(timeout=5)
    assert len(calls) >= 2


def test_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        Refresher(0)
