"""Shared HTTP client with rate limiting and retry/backoff.

Every outbound request to an external data provider goes through `RateLimitedClient`
so a sync job can never hammer Sleeper or ESPN:

  * Requests to the same host are spaced at least `min_interval` seconds apart.
  * 429 and 5xx responses are retried with exponential backoff, honoring `Retry-After`.
"""

import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RateLimiter:
    """Enforces a minimum delay between consecutive calls (thread-safe)."""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                remaining = self.min_interval - (now - self._last)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last = now


class RateLimitedClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 60.0,
        user_agent: str = "fantasy-cheat-code/0.1",
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ):
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}
        self._limiters_lock = threading.Lock()
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": user_agent}, transport=transport
        )

    def _limiter_for(self, url: str) -> RateLimiter:
        host = urlparse(url).netloc
        with self._limiters_lock:
            if host not in self._limiters:
                self._limiters[host] = RateLimiter(self._min_interval, sleep=self._sleep)
            return self._limiters[host]

    def get_json(self, url: str, params=None, headers: dict[str, str] | None = None):
        limiter = self._limiter_for(url)
        for attempt in range(self._max_retries + 1):
            limiter.wait()
            response = self._client.get(url, params=params, headers=headers)
            if response.status_code not in RETRY_STATUSES or attempt == self._max_retries:
                response.raise_for_status()
                return response.json()
            self._sleep(self._retry_delay(response, attempt))
        raise AssertionError("unreachable")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return self._backoff_base * (2**attempt)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RateLimitedClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
