"""Shared HTTP client with rate limiting and retry/backoff.

Every outbound request to an external data provider goes through `RateLimitedClient`
so a sync job can never hammer Sleeper or ESPN:

  * Requests to the same host are spaced at least `min_interval` seconds apart.
  * 429 and 5xx responses are retried with exponential backoff, honoring `Retry-After`.
  * Optional limits for request-time use: `max_response_bytes` (checked while the body
    streams in, after decompression, so an oversized or gzip-bomb body is never fully
    read), `total_timeout` (a deadline for the whole body, since `timeout` is per read),
    and `max_retry_delay` (if the server asks for a longer wait, fail instead of sleeping).
"""

import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ResponseTooLarge(httpx.HTTPError):
    """The response body exceeded the client's `max_response_bytes`."""


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
        max_response_bytes: int | None = None,
        total_timeout: float | None = None,
        max_retry_delay: float | None = None,
    ):
        self._min_interval = min_interval
        self._max_response_bytes = max_response_bytes
        self._total_timeout = total_timeout
        self._max_retry_delay = max_retry_delay
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}
        self._limiters_lock = threading.Lock()
        # follow_redirects: GitHub release downloads redirect to a storage host.
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            transport=transport,
            follow_redirects=True,
        )

    def _limiter_for(self, url: str) -> RateLimiter:
        host = urlparse(url).netloc
        with self._limiters_lock:
            if host not in self._limiters:
                self._limiters[host] = RateLimiter(self._min_interval, sleep=self._sleep)
            return self._limiters[host]

    def get_json(self, url: str, params=None, headers: dict[str, str] | None = None):
        return self._get(url, params, headers).json()

    def get_text(self, url: str, params=None, headers: dict[str, str] | None = None) -> str:
        """For CSV and other text downloads (e.g. nflverse files on GitHub releases)."""
        return self._get(url, params, headers).text

    def get_bytes(self, url: str, params=None, headers: dict[str, str] | None = None) -> bytes:
        """For binary downloads such as gzipped CSVs."""
        return self._get(url, params, headers).content

    def _get(self, url: str, params, headers: dict[str, str] | None) -> httpx.Response:
        limiter = self._limiter_for(url)
        for attempt in range(self._max_retries + 1):
            limiter.wait()
            response = self._read(url, params, headers)
            if response.status_code not in RETRY_STATUSES or attempt == self._max_retries:
                response.raise_for_status()
                return response
            delay = self._retry_delay(response, attempt)
            if self._max_retry_delay is not None and delay > self._max_retry_delay:
                response.raise_for_status()  # don't park the caller for a long Retry-After
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _read(self, url: str, params, headers: dict[str, str] | None) -> httpx.Response:
        """GET with the body streamed in, enforcing the size cap and overall deadline."""
        request = self._client.build_request("GET", url, params=params, headers=headers)
        response = self._client.send(request, stream=True)
        limit = self._max_response_bytes
        deadline = time.monotonic() + self._total_timeout if self._total_timeout else None
        try:
            declared = response.headers.get("Content-Length", "")
            # Content-Length is the compressed size, and decompressed is never smaller.
            if limit is not None and declared.isdigit() and int(declared) > limit:
                raise ResponseTooLarge(f"Response from {response.url.host} is larger than {limit} bytes")
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if limit is not None and total > limit:
                    raise ResponseTooLarge(f"Response from {response.url.host} is larger than {limit} bytes")
                if deadline is not None and time.monotonic() > deadline:
                    raise httpx.ReadTimeout(f"Response from {response.url.host} took too long", request=request)
                chunks.append(chunk)
        finally:
            response.close()
        # Rebuild a plain response from the decoded body (dropping encoding headers that no
        # longer describe it), so callers can use .json() and .text as before.
        kept = [(k, v) for k, v in response.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(response.status_code, headers=kept, content=b"".join(chunks),
                              request=response.request)

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
