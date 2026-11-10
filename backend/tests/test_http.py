import time

import httpx
import pytest

from app.data.sources.http import RateLimitedClient, RateLimiter, ResponseTooLarge


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



def test_size_cap_stops_reading_a_streamed_body():
    chunks_sent = []

    def body():
        for _ in range(1000):  # would be 64 MB if fully read
            chunks_sent.append(1)
            yield b"x" * 65536

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body()))
    with RateLimitedClient(min_interval=0, transport=transport, max_response_bytes=100_000) as client:
        with pytest.raises(ResponseTooLarge):
            client.get_text("https://example.test/stream")
    assert len(chunks_sent) < 5


def test_declared_length_over_the_cap_is_rejected_before_reading():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"Content-Length": "999999999"}, content=b"{}"))
    with RateLimitedClient(min_interval=0, transport=transport, max_response_bytes=1000) as client:
        with pytest.raises(ResponseTooLarge):
            client.get_json("https://example.test/big")


def test_gzip_bodies_are_measured_after_decompression():
    import gzip

    bomb = gzip.compress(b"0" * 5_000_000)  # about 5 KB compressed, 5 MB decoded
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, content=bomb))
    with RateLimitedClient(min_interval=0, transport=transport, max_response_bytes=1_000_000) as client:
        with pytest.raises(ResponseTooLarge):
            client.get_text("https://example.test/bomb")
    with RateLimitedClient(min_interval=0, transport=transport) as client:
        assert len(client.get_text("https://example.test/bomb")) == 5_000_000


def test_long_retry_after_fails_instead_of_sleeping():
    clock = FakeClock()
    transport = httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "300"}))
    with RateLimitedClient(min_interval=0, max_retries=2, transport=transport, sleep=clock.sleep,
                           max_retry_delay=2.0) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.get_json("https://example.test/limited")
    assert clock.sleeps == []


def test_total_timeout_stops_a_slow_body():
    def body():
        for _ in range(100):
            time.sleep(0.02)
            yield b"x"


    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body()))
    with RateLimitedClient(min_interval=0, transport=transport, total_timeout=0.1) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.get_text("https://example.test/slow")
