"""Concurrency stress tests: many threads at once against the shared, stateful pieces."""

import random
import threading
import time
from collections import Counter

import httpx

from app.api.ratelimit import RateLimiter, Rule
from app.data.profile_cache import CachedProfile, ProfileCache
from app.data.sources.espn_athletes import AthleteNotFound
from app.services.profiles import ProfileService, ProfileUnavailable


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now


class SlowFlakySource:
    """Takes a little time per call, fails some ids, and records peak concurrency."""

    def __init__(self, failing=(), missing=(), delay=0.01):
        self.failing, self.missing, self.delay = set(failing), set(missing), delay
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.bio_calls = Counter()

    def _enter(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def _exit(self):
        with self.lock:
            self.active -= 1

    def bio(self, espn_id):
        self._enter()
        try:
            with self.lock:
                self.bio_calls[espn_id] += 1
            time.sleep(self.delay)
            if espn_id in self.failing:
                raise httpx.ConnectError("down")
            if espn_id in self.missing:
                raise AthleteNotFound(str(espn_id))
            return {"name": f"Player {espn_id}"}
        finally:
            self._exit()

    def stats(self, espn_id, league):
        time.sleep(self.delay / 2)
        return [{"label": league}]


def _hammer(service, ids, threads=40, per_thread=25, seed=7):
    rng = random.Random(seed)
    plan = [[rng.choice(ids) for _ in range(per_thread)] for _ in range(threads)]
    outcomes = Counter()
    unexpected = []

    def worker(requests):
        for espn_id in requests:
            try:
                result = service.get(espn_id)
                outcomes[result.freshness] += 1
                assert result.profile.espn_id == espn_id
            except ProfileUnavailable as error:
                outcomes[f"unavailable:{error.reason}"] += 1
            except Exception as error:  # noqa: BLE001 - anything else is a bug
                unexpected.append(error)

    workers = [threading.Thread(target=worker, args=(requests,)) for requests in plan]
    for t in workers:
        t.start()
    for t in workers:
        t.join(30)
    assert not any(t.is_alive() for t in workers), "a request hung"
    return outcomes, unexpected


def test_profile_service_under_load(tmp_path):
    ids = list(range(1, 21))
    source = SlowFlakySource(failing={3, 7}, missing={11})
    service = ProfileService(source, ProfileCache(tmp_path / "p.db"), max_concurrent=2,
                             wait_timeout=5, clock=Clock())
    outcomes, unexpected = _hammer(service, ids)

    assert not unexpected, unexpected[:3]
    assert source.peak <= 2  # the concurrency cap held
    # Healthy players were fetched at most once each (single-flight + cache), failing
    # ones at most once (backoff), despite 1,000 requests.
    assert all(n == 1 for n in source.bio_calls.values()), source.bio_calls
    assert sum(outcomes.values()) == 40 * 25


def test_profile_cache_survives_concurrent_writers(tmp_path):
    cache = ProfileCache(tmp_path / "p.db")
    errors = []

    def writer(start):
        for i in range(start, start + 30):
            if not cache.put(CachedProfile(i, "ok", float(i), {"n": i}, [], [])):
                errors.append(i)
            cache.get(i)

    threads = [threading.Thread(target=writer, args=(k * 30,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert all(cache.get(i).bio == {"n": i} for i in range(240))


def test_rate_limiter_is_exact_under_contention():
    limiter = RateLimiter([Rule("r", burst=100, per_second=0.0001, matches=lambda p: True)])
    allowed = Counter()

    def worker(name):
        for _ in range(50):
            if limiter.check("shared-client", "/") is None:
                allowed[name] += 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(allowed.values()) == 100  # 1,000 attempts, exactly the burst got through


def test_rate_limiter_memory_stays_bounded_under_a_flood():
    limiter = RateLimiter([Rule("r", 5, 1, lambda p: True)], max_clients=500)

    def worker(offset):
        for i in range(2000):
            limiter.check(f"10.{offset}.{i // 256}.{i % 256}", "/")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(limiter._buckets) <= 500
