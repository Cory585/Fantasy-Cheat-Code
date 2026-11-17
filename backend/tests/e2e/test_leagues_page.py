import re
import secrets

import pytest
from playwright.sync_api import expect

from app.main import limiter
from app.models.league import MAX_LEAGUES
from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e


@pytest.fixture
def key(page):
    """A known profile key for the page, so tests can set up leagues through the API."""
    value = secrets.token_urlsafe(32)
    page.add_init_script(f"localStorage.setItem('fcc.profile.v1', '{value}')")
    return value


def add_league(page, key, name, roster=(), **slots):
    resp = page.request.post("/leagues", data={"name": name, "roster": list(roster), "slots": slots},
                             headers={"X-Profile-Key": key})
    assert resp.status == 201, resp.text()
    return resp.json()


def leagues(page, key):
    return page.request.get("/leagues", headers={"X-Profile-Key": key}).json()["leagues"]


def test_add_rename_and_remove_leagues(page, key):
    page.goto("/profile/")
    expect(page.locator("#league-count")).to_have_text(f"0 of {MAX_LEAGUES} leagues")
    for name in ("Work league", "Family league"):
        page.fill("#league-name", name)
        page.click("#league-add")
        expect(page.locator(".league-card-name", has_text=name)).to_be_visible()
    expect(page.locator("#league-count")).to_have_text(f"2 of {MAX_LEAGUES} leagues")
    assert [lg["name"] for lg in leagues(page, key)] == ["Work league", "Family league"]

    page.click('button[aria-label="Rename Work league"]')
    rename = page.locator('input[aria-label="New name for Work league"]')
    rename.fill("Office league")
    rename.press("Enter")
    expect(page.locator(".league-card-name", has_text="Office league")).to_be_visible()

    page.click('button[aria-label="Remove Family league"]')
    expect(page.locator(".league-card")).to_have_count(2)  # the first click only arms it
    page.click('button[aria-label="Confirm removing Family league"]')
    expect(page.locator(".league-card")).to_have_count(1)
    assert [lg["name"] for lg in leagues(page, key)] == ["Office league"]
    assert page.problems.errors == []


def test_rename_does_not_overwrite_a_change_made_elsewhere(page, key):
    league = add_league(page, key, "Work league")
    page.goto("/profile/")
    expect(page.locator(".league-card-name", has_text="Work league")).to_be_visible()
    # Another device renames it after this page loaded it.
    resp = page.request.put(f"/leagues/{league['id']}", data={"name": "Renamed elsewhere"},
                            headers={"X-Profile-Key": key})
    assert resp.status == 200

    page.click('button[aria-label="Rename Work league"]')
    rename = page.locator('input[aria-label="New name for Work league"]')
    rename.fill("Office league")
    rename.press("Enter")
    expect(page.locator("#profile-status")).to_contain_text("changed in another tab or on another device")
    # The page picks up the other device's version, and nothing was overwritten.
    rename = page.locator('input[aria-label="New name for Renamed elsewhere"]')
    expect(rename).to_have_value("Renamed elsewhere")
    assert [lg["name"] for lg in leagues(page, key)] == ["Renamed elsewhere"]

    rename.fill("Office league")
    rename.press("Enter")
    expect(page.locator(".league-card-name", has_text="Office league")).to_be_visible()
    assert [lg["name"] for lg in leagues(page, key)] == ["Office league"]


def test_save_made_while_another_is_in_flight_never_guesses_the_version(page, key):
    """The save sent as a page closes can start while an earlier save is still out. It waits
    for that one and sends the version it came back with; if it was refused, the later save
    sends the old version (and is refused too) rather than a guessed one that might match
    another device's save."""
    league = add_league(page, key, "Work league")
    page.goto("/profile/")
    expect(page.locator(".league-card-name", has_text="Work league")).to_be_visible()
    log = page.evaluate("""async (id) => {
        const log = [];
        const realFetch = window.fetch;
        let first = true;
        window.fetch = async (url, options = {}) => {
            if (options.method !== 'PUT') return realFetch(url, options);
            log.push({event: 'sent', ifMatch: options.headers['If-Match'], keepalive: options.keepalive});
            if (first) {
                first = false;
                // Another device saved first: this save is refused, after a delay.
                await new Promise((resolve) => setTimeout(resolve, 300));
                log.push({event: 'refused'});
                return new Response(JSON.stringify({detail: 'x', code: 'league_conflict',
                    league: {id, name: 'Elsewhere', slots: {}, roster: [], created: '', updated: '', version: 2}}),
                    {status: 409, headers: {'Content-Type': 'application/json'}});
            }
            return new Response(JSON.stringify({detail: 'x', code: 'league_conflict'}), {status: 409});
        };
        const one = LeagueStore.save(id, {name: 'One'}).catch((e) => e.conflict);
        const two = LeagueStore.save(id, {name: 'Two'}, {keepalive: true}).catch((e) => e.status);
        await Promise.all([one, two]);
        window.fetch = realFetch;
        return log;
    }""", league["id"])
    assert [entry["event"] for entry in log] == ["sent", "refused", "sent"]  # one at a time
    assert log[0]["ifMatch"] == log[2]["ifMatch"] == '"1"'  # the version this page saw, not a guess
    assert log[2]["keepalive"] is True


def test_profile_holds_at_most_ten_leagues(page, key):
    for i in range(MAX_LEAGUES):
        add_league(page, key, f"League {i + 1}")
    page.goto("/profile/")
    expect(page.locator(".league-card")).to_have_count(MAX_LEAGUES)
    expect(page.locator("#league-add")).to_be_disabled()
    expect(page.locator(".league-full")).to_contain_text(f"limit of {MAX_LEAGUES} leagues")


def test_league_errors_read_as_plain_language(page, key):
    page.route("**/leagues", lambda route: route.fulfill(status=502, body="Bad gateway"))
    page.goto("/profile/")
    expect(page.locator(".result-error")).to_have_text(
        "Your leagues can't be loaded or saved right now. Try again in a moment.")
    page.unroute("**/leagues")
    page.route("**/leagues", lambda route: route.fulfill(
        status=503, content_type="application/json",
        body='{"detail": "Leagues are being moved. Back soon.", "code": "leagues_unavailable"}'))
    page.reload()
    expect(page.locator(".result-error")).to_have_text("Leagues are being moved. Back soon.")
    assert "HTTP" not in page.locator("main").inner_text()


def test_rate_limited_changes_explain_the_wait(page, key):
    original = limiter.rules
    limiter.rules = [rule for rule in original if rule.name != "league-changes"] + [
        type(original[0])("league-changes", 1, 0.01, lambda p: p.startswith("/leagues"),
                          methods=frozenset({"POST", "DELETE"}))]
    try:
        page.goto("/profile/")
        page.fill("#league-name", "First")
        page.click("#league-add")
        expect(page.locator(".league-card")).to_have_count(1)
        page.fill("#league-name", "Second")
        page.click("#league-add")
        expect(page.locator("#profile-status")).to_contain_text("Too many league changes")
        expect(page.locator(".league-card")).to_have_count(1)
    finally:
        limiter.rules = original


def test_league_names_render_as_text(page, key):
    add_league(page, key, XSS_NAME[:40])
    page.goto("/profile/")
    expect(page.locator(".league-card")).to_have_count(1)
    page.goto("/team/")
    expect(page.locator("#league-select option")).to_have_count(1)
    assert page.evaluate("window.__pwned") is None


def test_team_page_switches_leagues(page, key):
    work = add_league(page, key, "Work", ["qb1", "rb1"])
    home = add_league(page, key, "Home", ["wr1", "wr2", "te1"], SUPERFLEX=1)
    page.goto(f"/team/?league={home['id']}")
    expect(page.locator("#league-select")).to_have_value(home["id"])
    expect(page.locator(".team-item")).to_have_count(3)
    page.select_option("#league-select", work["id"])
    expect(page.locator(".team-item")).to_have_count(2)
    expect(page.locator("#team-name-input")).to_have_value("Work")
    expect(page).to_have_url(re.compile(f"league={work['id']}"))
    # Edits go to the league being shown.
    search = page.locator("#team-search")
    search.fill("Sample WR Three")
    search.press("Enter")
    expect(page.locator("#save-status")).to_have_text("Added Sample WR Three.")
    saved = {lg["name"]: lg["roster"] for lg in leagues(page, key)}
    assert saved == {"Work": ["qb1", "rb1", "wr3"], "Home": ["wr1", "wr2", "te1"]}
    # The choice is remembered.
    page.goto("/team/")
    expect(page.locator("#league-select")).to_have_value(work["id"])
    assert page.problems.errors == []


def test_start_sit_switches_leagues(page, key):
    roster = ["qb1", "rb1", "rb2", "wr1", "wr2", "te1", "k1", "dst1"]
    add_league(page, key, "Work", roster)
    home = add_league(page, key, "Home", roster + ["qb2"], SUPERFLEX=1)
    page.goto("/start-sit/")
    expect(page.locator(".ss-lineup-head h2")).to_have_text("Work: week 1 lineup")
    expect(page.locator(".ss-spot")).to_have_count(9)
    page.select_option("#league-select", home["id"])
    expect(page.locator(".ss-lineup-head h2")).to_have_text("Home: week 1 lineup")
    expect(page.locator(".ss-spot")).to_have_count(10)
    expect(page.locator(".ss-lineup-head a")).to_have_attribute("href", f"/team/?league={home['id']}")
    # Compare mode offers players from the chosen league.
    page.click('#ss-mode button[data-mode="compare"]')
    expect(page.locator("#from-team-label")).to_have_text("From Home")
    expect(page.locator(".ss-quick-btn")).to_have_count(len(roster) + 1)
    assert page.problems.errors == []


def test_empty_league_points_to_its_roster(page, key):
    empty = add_league(page, key, "Fresh")
    page.goto("/start-sit/?mode=lineup")
    expect(page.locator(".ss-no-team a")).to_have_attribute("href", f"/team/?league={empty['id']}")


def test_profile_key_moves_leagues_to_another_browser(page, key, browser, base_url):
    add_league(page, key, "Portable")
    page.goto("/profile/")
    page.click("#key-show")
    expect(page.locator("#profile-key")).to_have_value(key)

    other = browser.new_context(base_url=base_url)
    try:
        second = other.new_page()
        second.goto("/profile/")
        expect(second.locator(".roster-empty")).to_be_visible()
        second.fill("#key-input", key)
        second.click("text=Switch profile")
        expect(second.locator(".league-card-name")).to_have_text("Portable")
    finally:
        other.close()


def test_switching_keys_asks_first_and_shows_the_current_key(page, key):
    add_league(page, key, "Keep me")
    other = secrets.token_urlsafe(32)
    add_league(page, other, "Over there")
    page.goto("/profile/")
    expect(page.locator(".league-card-name")).to_have_text("Keep me")
    page.fill("#key-input", other)
    page.click("text=Switch profile")
    # Nothing switched yet: the current key is shown so it can be copied first.
    expect(page.locator("#profile-status")).to_contain_text("Copy its key")
    expect(page.locator("#profile-key")).to_have_attribute("type", "text")
    expect(page.locator("#profile-key")).to_have_value(key)
    expect(page.locator(".league-card-name")).to_have_text("Keep me")
    assert page.evaluate("localStorage.getItem('fcc.profile.v1')") == key
    page.click("#key-form button:has-text('Confirm switch')")
    expect(page.locator(".league-card-name")).to_have_text("Over there")
    expect(page.locator("#profile-key")).to_have_value(other)
    expect(page.locator("#profile-key")).to_have_attribute("type", "password")


def test_profile_page_fits_a_phone(page, key):
    add_league(page, key, "A league with a fairly long name")
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("/profile/")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
