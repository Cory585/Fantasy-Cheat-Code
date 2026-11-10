from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.ratelimit import RateLimiter, Rule
from app.main import app, limiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(burst=2, per_second=1.0, **kw):
    clock = FakeClock()
    return RateLimiter([Rule("r", burst, per_second, lambda p: True)], clock=clock, **kw), clock


def test_allows_a_burst_then_blocks_until_refill():
    rl, clock = make(burst=2, per_second=0.5)
    assert rl.check("a", "/") is None
    assert rl.check("a", "/") is None
    assert rl.check("a", "/") == 2.0  # one token every 2 seconds
    clock.now = 2.0
    assert rl.check("a", "/") is None


def test_clients_are_independent():
    rl, _ = make(burst=1)
    assert rl.check("a", "/") is None
    assert rl.check("b", "/") is None
    assert rl.check("a", "/") is not None


def test_rejected_requests_do_not_drain_other_buckets():
    clock = FakeClock()
    rl = RateLimiter([
        Rule("all", 5, 1, lambda p: True),
        Rule("strict", 1, 0.1, lambda p: p == "/x"),
    ], clock=clock)
    assert rl.check("a", "/x") is None
    assert rl.check("a", "/x") is not None  # strict bucket empty
    for _ in range(4):  # the "all" bucket still has 4 tokens left
        assert rl.check("a", "/y") is None


def test_memory_is_bounded():
    rl, _ = make(max_clients=3)
    for i in range(10):
        rl.check(f"client-{i}", "/")
    assert len(rl._buckets) == 3


def _request(headers=None, host="10.0.0.1"):
    scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": (host, 1234)}
    return Request(scope)


def test_forwarded_for_is_ignored_unless_trusted():
    spoofed = _request({"x-forwarded-for": "1.2.3.4"})
    assert make()[0].client_key(spoofed) == "10.0.0.1"
    assert make(trust_proxy=True)[0].client_key(spoofed) == "1.2.3.4"


def test_api_returns_429_with_retry_after():
    client = TestClient(app)
    original = limiter.rules
    limiter.rules = [Rule("tiny", 1, 0.01, lambda p: True)]
    try:
        assert client.get("/health").status_code == 200
        resp = client.get("/health")
        assert resp.status_code == 429
        assert resp.json()["code"] == "rate_limited"
        assert int(resp.headers["Retry-After"]) >= 1
    finally:
        limiter.rules = original
