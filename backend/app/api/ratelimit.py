"""Per-client rate limiting for incoming requests (token buckets, in memory).

Each rule is a bucket per client: it holds up to `burst` requests and refills at
`per_second`. A request draws one token from every rule whose path (and, if the rule
names any, method) matches, and is rejected with 429 (and a `Retry-After` header) when
any of them is empty.

Clients are keyed by IP address. Behind a reverse proxy every request comes from the
proxy, so set TRUST_PROXY=1 to key on `X-Forwarded-For` instead. Proxies append the
address they saw to whatever the client sent, so only the rightmost entries are
trustworthy: with N proxies in front of the app (TRUSTED_PROXY_HOPS, default 1), the
client is the Nth entry from the right. Entries further left are client-controlled.

Buckets live in a bounded LRU map, so a flood of distinct addresses can't exhaust memory.
State is per process; running several workers multiplies the effective limit.
"""

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request


@dataclass(frozen=True)
class Rule:
    name: str
    burst: int
    per_second: float
    matches: Callable[[str], bool]
    # Methods the rule applies to (e.g. {"POST", "DELETE"}); empty means every method.
    methods: frozenset[str] = frozenset()

    def applies(self, path: str, method: str) -> bool:
        return (not self.methods or method.upper() in self.methods) and self.matches(path)


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, tokens: float, updated: float):
        self.tokens = tokens
        self.updated = updated


class RateLimiter:
    def __init__(
        self,
        rules: list[Rule],
        max_clients: int = 10_000,
        trust_proxy: bool = False,
        proxy_hops: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.rules = rules
        self.max_clients = max_clients
        self.trust_proxy = trust_proxy
        self.proxy_hops = max(1, proxy_hops)
        self.enabled = True
        self._clock = clock
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def client_key(self, request: Request) -> str:
        if self.trust_proxy:
            entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]
            if len(entries) >= self.proxy_hops:
                return entries[-self.proxy_hops][:64]
        return request.client.host if request.client else "unknown"

    def check(self, client: str, path: str, method: str = "GET") -> float | None:
        """Take a token from each matching bucket. Returns None if allowed, else seconds to wait."""
        if not self.enabled:
            return None
        now = self._clock()
        with self._lock:
            matching = [rule for rule in self.rules if rule.applies(path, method)]
            buckets = [(rule, self._bucket(client, rule, now)) for rule in matching]
            waits = [(1 - b.tokens) / rule.per_second for rule, b in buckets if b.tokens < 1]
            if waits:
                return max(waits)
            for _, bucket in buckets:
                bucket.tokens -= 1
            return None

    def _bucket(self, client: str, rule: Rule, now: float) -> _Bucket:
        key = (client, rule.name)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(rule.burst, now)
            self._buckets[key] = bucket
            if len(self._buckets) > self.max_clients:
                self._buckets.popitem(last=False)  # evict the least recently used
        else:
            bucket.tokens = min(rule.burst, bucket.tokens + (now - bucket.updated) * rule.per_second)
            bucket.updated = now
            self._buckets.move_to_end(key)
        return bucket

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


def retry_after_header(wait: float) -> str:
    return str(max(1, math.ceil(wait)))
