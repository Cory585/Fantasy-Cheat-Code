"""Screen reader and keyboard checks: skip link, headings, status regions, errors, focus."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

PAGES = ["/", "/finder/", "/draft/", "/tiers/", "/start-sit/", "/team/", "/profile/", "/defense/",
         "/player/?id=9221", "/no-such-page"]


@pytest.mark.parametrize("path", PAGES)
def test_skip_link_is_the_first_stop_and_jumps_past_the_nav(page, path):
    page.goto(path)
    skip = page.locator(".skip-link")
    expect(skip).not_to_be_in_viewport()
    page.keyboard.press("Tab")
    expect(skip).to_be_focused()
    expect(skip).to_be_in_viewport()
    expect(skip).to_have_text("Skip to content")
    page.keyboard.press("Enter")
    target = skip.get_attribute("href")
    expect(page.locator(target)).to_be_focused()
    # The next Tab lands after the nav, not on its first tab.
    page.keyboard.press("Tab")
    assert page.evaluate("!document.activeElement.closest('.site-nav')")


def test_result_sections_are_not_live_regions(page):
    for path, selector in [("/", "#result, .roster"), ("/start-sit/", "#ss-result, #ss-lineup, #ss-chosen"),
                           ("/team/", "#team-roster"), ("/profile/", "#league-list")]:
        page.goto(path)
        for el in page.locator(selector).all():
            assert el.get_attribute("aria-live") is None, (path, el.get_attribute("id"))


def test_trade_changes_are_announced_in_one_line(page):
    page.goto("/")
    status = page.locator("#trade-announce")
    expect(status).to_have_attribute("role", "status")
    search = page.locator('.side[data-side="a"] .player-search')
    search.fill("Sample RB One")
    search.press("Enter")
    expect(status).to_have_text("Added Sample RB One to Team A.")
    search = page.locator('.side[data-side="b"] .player-search')
    search.fill("Sample WR Two")
    search.press("Enter")
    # Once both sides have a player, the verdict replaces it.
    expect(status).to_have_text(re.compile(r"^Team [AB] wins|even", re.I))
    page.click("#clear")
    expect(status).to_have_text("Cleared the trade.")


def test_start_sit_call_is_announced_in_one_line(page):
    page.goto("/start-sit/?p=wr1,wr3&week=4")
    status = page.locator("#ss-result-announce")
    expect(status).to_have_attribute("role", "status")
    expect(status).to_have_text(re.compile(r"^Start Sample WR One\. (Confident call|Lean|Toss-up)$"))


def test_tier_names_are_headings(page):
    page.goto("/tiers/")
    expect(page.get_by_role("heading", name="Tier 1", level=2)).to_be_visible()


def test_trade_sides_have_headings(page):
    page.goto("/")
    for name in ("Team A", "Team B"):
        expect(page.get_by_role("heading", name=name, level=2)).to_be_attached()
