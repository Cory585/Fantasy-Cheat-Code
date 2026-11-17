import re
import time
from email.utils import formatdate

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e


def test_full_profile(page):
    page.goto("/player/?id=9221")
    expect(page.locator("#profile-name")).to_have_text("Jahmyr Gibbs")
    expect(page).to_have_title(re.compile("Jahmyr Gibbs"))
    expect(page.locator(".bio-fact")).to_have_count(7)
    expect(page.locator(".drill")).to_have_count(2)
    expect(page.locator(".drill-fill").first).to_have_attribute("style", re.compile("width: 98%"))
    expect(page.locator("#profile-photo img")).to_have_attribute("src", re.compile(r"^https://a\.espncdn\.com/"))
    expect(page.locator("#profile-photo img")).to_have_attribute("referrerpolicy", "no-referrer")
    expect(page.locator("#profile-notice")).to_be_hidden()
    assert page.problems.errors == []


def test_stat_category_buttons_switch_tables(page):
    page.goto("/player/?id=9221")
    nfl = page.locator("#nfl-stats")
    expect(nfl.locator(".stat-tabs button")).to_have_count(2)
    expect(nfl.locator('.stat-tabs button[aria-pressed="true"]')).to_have_text("Rushing")  # RB default
    expect(nfl.locator("tfoot td").last).to_have_text("3,580")
    nfl.locator(".stat-tabs button", has_text="Receiving").click()
    expect(nfl.locator('.stat-tabs button[aria-pressed="true"]')).to_have_text("Receiving")
    expect(nfl.locator("tfoot td").last).to_have_text("1,233")
    expect(nfl.locator(".season-chart .season-bar")).to_have_count(3)


def test_unknown_player(page):
    page.goto("/player/?id=000000")
    expect(page.locator("#profile-name")).to_have_text("Player not found")
    expect(page.locator("#profile-retry")).to_be_hidden()
    expect(page.locator(".notice-link")).to_have_count(1)


def test_invalid_id_never_calls_the_api(page):
    requests = []
    page.on("request", lambda r: "/players/" in r.url and requests.append(r.url))
    page.goto("/player/?id=%3Cscript%3E")
    expect(page.locator("#profile-name")).to_have_text("Player not found")
    assert requests == []


def test_markup_in_player_data_is_shown_as_text(page):
    page.goto("/player/?id=666")
    expect(page.locator("#profile-name")).to_have_text(XSS_NAME)
    assert page.evaluate("window.__pwned") is None
    expect(page.locator("main img")).to_have_count(1)  # only the headshot


def test_espn_unavailable_shows_local_data_and_a_countdown(page):
    page.clock.install()
    page.goto("/player/?id=500")
    expect(page.locator("#profile-notice-text")).to_contain_text("ESPN couldn't be reached")
    retry = page.locator("#profile-retry")
    expect(retry).to_be_disabled()
    expect(retry).to_have_text("Try again in 30s")
    page.clock.run_for(31_000)
    expect(retry).to_be_enabled()
    expect(retry).to_have_text("Try again")


def test_rate_limit_retry_after_can_be_a_date(page):
    page.clock.install()
    page.route("**/players/9221/profile", lambda route: route.fulfill(
        status=429, content_type="application/json", body='{"detail": "slow down", "code": "rate_limited"}',
        headers={"Retry-After": formatdate(time.time() + 8, usegmt=True)}))
    page.goto("/player/?id=9221")
    # An HTTP date 8 seconds ahead, not the 10-second fallback for a missing header.
    expect(page.locator("#profile-retry")).to_have_text(re.compile(r"Try again in [78]s"))


def test_rate_limited_profile_counts_down_once_per_second(page):
    page.clock.install()
    page.route("**/players/9221/profile", lambda route: route.fulfill(
        status=429, headers={"Retry-After": "5"}, content_type="application/json",
        body='{"detail": "slow down", "code": "rate_limited"}'))
    page.goto("/player/?id=9221")
    retry = page.locator("#profile-retry")
    expect(retry).to_have_text("Try again in 5s")
    page.clock.run_for(5_100)
    expect(retry).to_be_enabled()
    retry.click()  # 429 again: a second countdown must replace the first, not stack with it
    expect(retry).to_have_text("Try again in 5s")
    page.clock.run_for(2_050)
    expect(retry).to_have_text("Try again in 3s")
    expect(retry).to_be_disabled()


def test_offline_shows_a_retry(page):
    page.route("**/players/9221/profile", lambda route: route.abort())
    page.goto("/player/?id=9221")
    expect(page.locator("#profile-name")).to_have_text("Couldn't load this player")
    expect(page.locator("#profile-retry")).to_be_visible()
    page.unroute("**/players/9221/profile")
    page.click("#profile-retry")
    expect(page.locator("#profile-name")).to_have_text("Jahmyr Gibbs")


def test_a_new_countdown_replaces_the_old_one(page):
    page.clock.install()
    page.goto("/player/?id=9221")
    expect(page.locator("#profile-name")).to_have_text("Jahmyr Gibbs")
    # Two notices back to back (e.g. ESPN unavailable, then rate limited on retry).
    page.evaluate("showNotice('first', {retry: true, retryAfter: 10}); showNotice('second', {retry: true, retryAfter: 3})")
    retry = page.locator("#profile-retry")
    page.clock.run_for(3_100)
    expect(retry).to_be_enabled()
    page.clock.run_for(2_000)  # a leftover 10 s timer would now be rewriting the label
    expect(retry).to_have_text("Try again")
    expect(retry).to_be_enabled()
