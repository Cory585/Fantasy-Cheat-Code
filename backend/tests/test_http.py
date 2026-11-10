import httpx
import pytest

from app.data.sources.http import RateLimitedClient, RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 0.25
    limiter.wait()
    assert clock.sleeps == [pytest.approx(0.75)]


def test_rate_limiter_no_wait_when_interval_passed():
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 5
    limiter.wait()
    assert clock.sleeps == []


def test_client_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    with RateLimitedClient(
        min_interval=0, sleep=sleeps.append, transport=httpx.MockTransport(handler)
    ) as client:
        assert client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 2
    assert 3.0 in sleeps


def test_client_gives_up_after_max_retries():
    handler = lambda request: httpx.Response(503)
    with RateLimitedClient(
        min_interval=0, max_retries=2, sleep=lambda s: None, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.get_json("https://example.test/x")


def test_get_text_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.example":
            return httpx.Response(302, headers={"Location": "https://storage.example/file.csv"})
        return httpx.Response(200, text="a,b\n1,2\n")

    with RateLimitedClient(
        min_interval=0, sleep=lambda s: None, transport=httpx.MockTransport(handler)
    ) as client:
        assert client.get_text("https://github.example/file.csv") == "a,b\n1,2\n"


def test_client_rejects_oversized_responses():
    from app.data.sources.http import ResponseTooLarge

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 2000))
    with RateLimitedClient(min_interval=0, transport=transport, max_response_bytes=1000) as client:
        with pytest.raises(ResponseTooLarge):
            client.get_text("https://example.test/big")
    with RateLimitedClient(min_interval=0, transport=transport) as client:
        assert len(client.get_text("https://example.test/big")) == 2000  # no limit by default
