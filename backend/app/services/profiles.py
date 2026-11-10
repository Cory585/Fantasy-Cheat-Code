"""Fetch ESPN player profiles on demand, with caching and protection for ESPN.

When a profile is requested:

1. A cached profile younger than `ttl` is returned without contacting ESPN. Players ESPN
   doesn't know are cached too (for `not_found_ttl`), so they aren't re-fetched.
2. Otherwise it's fetched (bio, NFL stats, college stats: three requests through the
   rate-limited client) and cached.
3. Concurrent requests for the same player share one fetch (single-flight).
4. At most `max_concurrent` fetches run at once. Beyond that, and for `failure_backoff`
   seconds after a player's fetch fails, the stale cached copy is served if there is one;
   otherwise the caller is told ESPN is unavailable. A slow or failing ESPN never queues
   up requests or gets retried in a tight loop.

The API only asks for players in its own player pool, so arbitrary ids can't be used to
make the server crawl ESPN.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.data.profile_cache import CachedProfile, ProfileCache
from app.data.sources.espn_athletes import AthleteNotFound, valid_espn_id

log = logging.getLogger(__name__)


class ProfileUnavailable(Exception):
    """ESPN couldn't be reached (or is busy) and there's no cached copy."""

    def __init__(self, reason: str, retry_after: float):
        super().__init__(reason)
        self.reason = reason  # "busy" or "upstream_error"
        self.retry_after = retry_after


@dataclass
class ProfileResult:
    profile: CachedProfile
    freshness: str  # "fresh" (just fetched), "cached", or "stale" (ESPN failed, older copy)


class ProfileService:
    def __init__(
        self,
        source,  # EspnAthleteSource or a stand-in with bio() and stats()
        cache: ProfileCache,
        ttl: float = 24 * 3600,
        not_found_ttl: float = 7 * 24 * 3600,
        failure_backoff: float = 600,
        max_concurrent: int = 2,
        clock: Callable[[], float] = time.time,
    ):
        self._source = source
        self._cache = cache
        self._ttl = ttl
        self._not_found_ttl = not_found_ttl
        self._failure_backoff = failure_backoff
        self._clock = clock
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._failures: dict[int, float] = {}

    def get(self, espn_id: int) -> ProfileResult:
        if not valid_espn_id(espn_id):
            raise ValueError("Invalid ESPN id")
        cached = self._cache.get(espn_id)
        if cached and self._is_fresh(cached):
            return ProfileResult(cached, "cached")

        failed_at = self._failures.get(espn_id)
        if failed_at is not None and self._clock() - failed_at < self._failure_backoff:
            return self._fallback(cached, "upstream_error", self._failure_backoff - (self._clock() - failed_at))

        with self._lock_for(espn_id):
            # Another request may have fetched it while this one waited for the lock.
            cached = self._cache.get(espn_id) or cached
            if cached and self._is_fresh(cached):
                return ProfileResult(cached, "cached")
            if not self._slots.acquire(blocking=False):
                return self._fallback(cached, "busy", 5)
            try:
                profile = self._fetch(espn_id)
            except (httpx.HTTPError, ValueError) as error:
                log.warning("ESPN profile fetch failed for %s: %s", espn_id, error)
                self._failures[espn_id] = self._clock()
                return self._fallback(cached, "upstream_error", self._failure_backoff)
            finally:
                self._slots.release()
        self._failures.pop(espn_id, None)
        self._cache.put(profile)
        return ProfileResult(profile, "fresh")

    def _is_fresh(self, cached: CachedProfile) -> bool:
        ttl = self._not_found_ttl if cached.status == "not_found" else self._ttl
        return self._clock() - cached.fetched_at < ttl

    def _fallback(self, cached: CachedProfile | None, reason: str, retry_after: float) -> ProfileResult:
        if cached:
            return ProfileResult(cached, "stale")
        raise ProfileUnavailable(reason, retry_after)

    def _lock_for(self, espn_id: int) -> threading.Lock:
        with self._locks_guard:
            if len(self._locks) > 1000:  # bounded; idle locks are cheap to recreate
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            return self._locks.setdefault(espn_id, threading.Lock())

    def _fetch(self, espn_id: int) -> CachedProfile:
        now = self._clock()
        try:
            bio = self._source.bio(espn_id)
        except AthleteNotFound:
            return CachedProfile(espn_id, "not_found", now, None, [], [])
        return CachedProfile(espn_id, "ok", now, bio, self._stats(espn_id, "nfl"), self._stats(espn_id, "college"))

    def _stats(self, espn_id: int, league: str) -> list:
        try:
            return self._source.stats(espn_id, league)
        except AthleteNotFound:
            return []  # e.g. a rookie with no NFL stats yet, or no college record
