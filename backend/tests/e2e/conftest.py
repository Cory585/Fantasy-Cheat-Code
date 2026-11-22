"""End-to-end browser tests: the real app served by uvicorn, driven in Microsoft Edge (or
Chrome) through Playwright. Hermetic: fake player data, a fake ESPN profile service, and
every request to another host is answered locally, so nothing leaves the machine.

Skipped automatically if Playwright or a supported browser isn't installed. Run only these
with `pytest -m e2e`, or skip them with `pytest -m "not e2e"`.
"""

import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from app.api.dependencies import get_adp, get_local_player_data, get_player_repository, get_profile_service  # noqa: E402
from app.data.profile_cache import CachedProfile  # noqa: E402
from app.data.repository import InMemoryPlayerRepository  # noqa: E402
from app.data.sample_players import SAMPLE_PLAYERS  # noqa: E402
from app.main import app, limiter  # noqa: E402
from app.models.player import Player, Position  # noqa: E402
from app.services.player_profile import LocalPlayerData  # noqa: E402
from app.services.profiles import ProfileResult, ProfileUnavailable  # noqa: E402

XSS_NAME = '<img src=x onerror="window.__pwned=1">'

PLAYERS = SAMPLE_PLAYERS + [
    Player(id="9221", name="Jahmyr Gibbs", position=Position.RB, team="DET", espn_id=4429795, ros_projection=230,
           college="Alabama"),
    Player(id="666", name=XSS_NAME, position=Position.WR, team="DET", espn_id=666, ros_projection=1),
    Player(id="500", name="Down Espn", position=Position.TE, team="DET", espn_id=500, ros_projection=2),
]

BIO = {"name": "Jahmyr Gibbs", "height": "5' 9\"", "weight": "202 lbs", "age": 24, "college": "Alabama",
       "draft": "2023: Rd 1, Pk 12 (DET)", "experience": "4th Season", "birth_place": "Dalton, GA",
       "jersey": "#0", "status": "Active",
       "headshot_url": "https://a.espncdn.com/i/headshots/nfl/players/full/4429795.png"}


def _category(name, label, column, values):
    return {"name": name, "label": label, "columns": [{"label": "GP", "name": "Games"}, {"label": column, "name": column}],
            "seasons": [{"year": 2023 + i, "team": "DET", "values": [17, v]} for i, v in enumerate(values)],
            "totals": [51, sum(values)]}


NFL = [_category("rushing", "Rushing", "YDS", [945, 1412, 1223]), _category("receiving", "Receiving", "YDS", [316, 517, 400])]
COLLEGE = [_category("rushing", "Rushing", "YDS", [460, 746, 926])]


class FakeProfiles:
    def get(self, espn_id):
        if espn_id == 500:
            raise ProfileUnavailable("upstream_error", 30)
        bio = dict(BIO, name=XSS_NAME) if espn_id == 666 else BIO
        return ProfileResult(CachedProfile(espn_id, "ok", time.time(), bio, NFL, COLLEGE), "cached")


class FakeLocal(LocalPlayerData):
    def __init__(self):
        super().__init__(None)

    def lookup(self, espn_id):
        from app.models.profile import Combine, CombineDrill

        if espn_id != 4429795:
            return None, None
        drills = [CombineDrill(key="forty", label="40-yard dash", value=4.36, unit="s", lower_is_better=True,
                               percentile=98, compared_with=716),
                  CombineDrill(key="vertical", label="Vertical jump", value=33.5, unit="in", lower_is_better=False,
                               percentile=38, compared_with=632)]
        return None, Combine(season=2023, school="Alabama", position="RB", drills=drills)


@pytest.fixture(scope="session")
def base_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("test server didn't start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        launched = None
        for channel in ("msedge", "chrome", None):
            try:
                launched = p.chromium.launch(channel=channel, headless=True) if channel else p.chromium.launch(headless=True)
                break
            except Exception:  # noqa: BLE001 - try the next browser
                continue
        if launched is None:
            pytest.skip("No browser available for Playwright (install Edge or Chrome)")
        yield launched
        launched.close()


@pytest.fixture(autouse=True)
def e2e_data():
    """Runs after the root conftest's overrides, replacing them with e2e data."""
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(PLAYERS)
    app.dependency_overrides[get_adp] = lambda: {}
    app.dependency_overrides[get_profile_service] = FakeProfiles
    app.dependency_overrides[get_local_player_data] = FakeLocal
    limiter.reset()
    yield


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class PageProblems:
    """Collects console errors, page errors, and CSP violations seen by a page."""

    def __init__(self, page):
        self.errors = []
        page.on("console", lambda msg: msg.type == "error" and self.errors.append(f"console: {msg.text}"))
        page.on("pageerror", lambda err: self.errors.append(f"pageerror: {err}"))
        page.add_init_script("""
            window.__csp = [];
            document.addEventListener('securitypolicyviolation',
              e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
        """)


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 900})

    def offline(route):
        url = route.request.url
        if "fonts.googleapis.com" in url:
            return route.fulfill(status=200, content_type="text/css", body="")
        if "a.espncdn.com" in url:
            # ESPN's CDN allows cross-origin reads; the backdrop's dithered portrait needs that.
            return route.fulfill(status=200, content_type="image/png", body=TINY_PNG,
                                 headers={"Access-Control-Allow-Origin": "*"})
        return route.fulfill(status=204, body="")

    context.route(lambda url: not url.startswith(base_url), offline)
    page = context.new_page()
    page.problems = PageProblems(page)
    yield page
    csp = page.evaluate("window.__csp || []")
    context.close()
    assert not csp, f"CSP violations: {csp}"
