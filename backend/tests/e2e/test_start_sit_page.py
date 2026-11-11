import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e


def test_comparison_from_the_url(page):
    page.goto("/start-sit/?p=wr1,wr3&week=4")
    expect(page.locator(".ss-verdict h2")).to_have_text("Start Sample WR One")
    expect(page.locator("#week")).to_have_value("4")
    expect(page.locator(".ss-chip")).to_have_count(2)
    expect(page.locator(".ss-card")).to_have_count(2)
    expect(page.locator(".ss-card.is-start .player-name")).to_have_text("Sample WR One")
    expect(page.locator(".ss-range-row")).to_have_count(2)
    expect(page.locator(".ss-verdict .ss-badge")).to_have_text(re.compile("Confident call|Lean|Toss-up"))
    assert page.problems.errors == []


def test_adding_players_with_the_keyboard(page):
    page.goto("/start-sit/")
    expect(page.locator(".result-empty")).to_contain_text("at least two")
    search = page.locator("#ss-search")
    search.fill("Sample RB One")
    search.press("Enter")
    expect(page.locator(".result-empty")).to_contain_text("one more")
    # With an RB chosen and "Any" spot, kickers and defenses aren't offered.
    search.fill("Sample Kicker")
    expect(page.locator("#ss-results .search-empty")).to_contain_text("No players match")
    search.fill("Sample WR Two")
    search.press("Enter")
    expect(page.locator(".ss-verdict")).to_contain_text("flex spot")
    expect(page).to_have_url(re.compile(r"p=rb1%2Cwr2|p=rb1,wr2"))


def test_slot_change_drops_players_who_dont_fit(page):
    page.goto("/start-sit/?p=rb1,wr1,wr2&week=2")
    expect(page.locator(".ss-card")).to_have_count(3)
    page.click('#slot button[data-slot="WR"]')
    expect(page.locator(".ss-chip")).to_have_count(2)
    expect(page.locator(".ss-verdict")).to_contain_text("WR spot")


def test_starts_and_week_controls(page):
    page.goto("/start-sit/?p=wr1,wr2,wr3&week=1")
    expect(page.locator(".ss-card")).to_have_count(3)
    expect(page.locator("#week-prev")).to_be_disabled()
    page.click('#starts button[data-starts="2"]')
    expect(page.locator(".ss-card.is-start")).to_have_count(2)
    expect(page.locator('#starts button[data-starts="3"]')).to_be_disabled()
    page.click("#week-next")
    expect(page.locator(".ss-verdict")).to_contain_text("Week 2")
    expect(page).to_have_url(re.compile(r"week=2.*starts=2|starts=2.*week=2"))


def test_removing_a_player_updates_the_call(page):
    page.goto("/start-sit/?p=wr1,wr2&week=3")
    expect(page.locator(".ss-card")).to_have_count(2)
    page.click('button[aria-label="Remove Sample WR One"]')
    expect(page.locator(".result-empty")).to_contain_text("one more")
    assert page.evaluate("document.activeElement.id") == "ss-search"


def test_bye_week_player_is_benched(page):
    # Sample WR One (MIN) has a week 6 bye.
    page.goto("/start-sit/?p=wr1,wr3&week=6")
    expect(page.locator(".ss-verdict h2")).to_have_text("Start Sample WR Three")
    expect(page.locator(".warnings")).to_contain_text("on bye")


def test_server_error_is_shown(page):
    page.route("**/start-sit?*", lambda route: route.fulfill(
        status=422, content_type="application/json", body='{"detail": "Week must be between 1 and 17.", "code": "invalid_week"}'))
    page.goto("/start-sit/?p=wr1,wr2")
    expect(page.locator(".result-error")).to_have_text("Week must be between 1 and 17.")


def test_player_names_render_as_text(page):
    page.goto("/start-sit/?p=666,wr1&week=2")
    expect(page.locator(".ss-card")).to_have_count(2)
    expect(page.locator(".ss-chip-name", has_text=XSS_NAME)).to_have_count(1)
    assert page.evaluate("window.__pwned") is None


def test_fits_a_phone_screen(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("/start-sit/?p=wr1,wr2,rb1&week=2")
    expect(page.locator(".ss-card")).to_have_count(3)
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
