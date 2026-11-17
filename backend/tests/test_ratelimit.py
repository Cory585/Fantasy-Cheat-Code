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
    forwarded = _request({"x-forwarded-for": "1.2.3.4"})
    assert make()[0].client_key(forwarded) == "10.0.0.1"
    assert make(trust_proxy=True)[0].client_key(forwarded) == "1.2.3.4"


def test_client_supplied_forwarded_entries_are_ignored():
    # The client sent "spoofed"; the proxy appended the address it actually saw.
    request = _request({"x-forwarded-for": "spoofed, 1.2.3.4"})
    assert make(trust_proxy=True)[0].client_key(request) == "1.2.3.4"
    # Two proxies: the client is second from the right.
    chained = _request({"x-forwarded-for": "spoofed, 5.6.7.8, 10.1.1.1"})
    assert make(trust_proxy=True, proxy_hops=2)[0].client_key(chained) == "5.6.7.8"
    # Fewer entries than hops: fall back to the connecting address.
    assert make(trust_proxy=True, proxy_hops=3)[0].client_key(request) == "10.0.0.1"


def test_ipv6_clients_are_keyed_by_their_64_network():
    rl = make()[0]
    a = rl.client_key(_request(host="2001:db8:1:2:aaaa::1"))
    b = rl.client_key(_request(host="2001:db8:1:2:ffff:ffff:ffff:ffff"))
    other = rl.client_key(_request(host="2001:db8:1:3::1"))
    assert a == b == "2001:db8:1:2::/64"
    assert other != a
    assert rl.client_key(_request(host="fe80::1%eth0")) == "fe80::/64"
    assert rl.client_key(_request(host="::ffff:10.0.0.9")) == "10.0.0.9"
    assert rl.client_key(_request(host="10.0.0.9")) == "10.0.0.9"
    assert rl.client_key(_request(host="testclient")) == "testclient"
    # Forwarded addresses are grouped the same way.
    forwarded = _request({"x-forwarded-for": "2001:db8:9:9::5"})
    assert make(trust_proxy=True)[0].client_key(forwarded) == "2001:db8:9:9::/64"


def test_rotating_ipv6_addresses_share_a_bucket():
    rl, _ = make(burst=2, per_second=0.01)
    keys = [rl.client_key(_request(host=f"2001:db8::{i:x}")) for i in range(1, 4)]
    assert [rl.check(key, "/") for key in keys][:2] == [None, None]
    assert rl.check(keys[2], "/") is not None


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


def test_rules_can_be_limited_to_methods():
    clock = FakeClock()
    rl = RateLimiter([Rule("writes", 1, 0.1, lambda p: True, methods=frozenset({"POST", "DELETE"}))], clock=clock)
    assert rl.check("a", "/x", "POST") is None
    assert rl.check("a", "/x", "delete") is not None  # same bucket, case-insensitive
    for _ in range(5):
        assert rl.check("a", "/x", "GET") is None  # reads aren't limited by this rule


def test_profile_rule_only_covers_player_profiles():
    upstream = next(rule for rule in limiter.rules if rule.name == "upstream")
    assert upstream.applies("/players/123/profile", "GET")
    assert not upstream.applies("/profile", "GET")
    assert not upstream.applies("/profile/", "GET")


def test_league_rules_split_changes_from_edits():
    rules = {rule.name: rule for rule in limiter.rules}
    changes, edits = rules["league-changes"], rules["league-edits"]
    assert changes.applies("/leagues", "POST")
    assert changes.applies("/leagues/abc123def", "DELETE")
    assert not changes.applies("/leagues", "GET")
    assert not changes.applies("/leagues/abc123def", "PUT")
    assert not changes.applies("/leaguesx", "POST")
    assert edits.applies("/leagues/abc123def", "PUT")
    assert changes.burst < edits.burst
