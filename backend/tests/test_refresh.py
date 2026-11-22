import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.data import refresh
from app.data.refresh import Refresher, cache_age_hours
from app.data.sync import SyncError


def write_cache(path, hours_ago):
    synced = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    path.write_text(json.dumps({"synced_at": synced.isoformat(), "players": []}), encoding="utf-8")


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


def test_runs_the_job_and_survives_failures(tmp_path):
    ran = threading.Event()
    calls = []

    def job():
        calls.append(1)
        ran.set()
        raise RuntimeError("provider down")

    refresher = Refresher(6, job=job, cache_path=tmp_path / "missing.json")
    refresher.start()
    assert ran.wait(5)
    refresher.stop(timeout=5)
    assert calls == [1] and refresher.runs == 1


def test_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        Refresher(0)


def test_refresh_once_reports_failures_and_keeps_going(monkeypatch):
    collected = []

    def failing_sync(*args, **kwargs):
        raise SyncError("no players")

    monkeypatch.setattr(refresh, "sync", failing_sync)
    monkeypatch.setattr(refresh, "collect", lambda datasets, seasons, conn: collected.append(seasons) or {"x:1": 3})
    monkeypatch.setattr(refresh.db, "connect", lambda path: type("Conn", (), {"close": lambda self: None})())
    assert refresh.refresh_once() is False
    assert len(collected) == 1  # the collect still ran
