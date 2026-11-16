import re

import pytest
from playwright.sync_api import expect

from app.api.dependencies import get_matchups
from app.main import app
from app.services.matchups import MatchupData

pytestmark = pytest.mark.e2e


def open_tiers(page, query=""):
    page.goto(f"/tiers/{query}")
    expect(page.locator(".tier-row").first).to_be_visible()


def test_opens_on_running_backs_for_the_rest_of_the_season(page):
    open_tiers(page)
    expect(page.locator('#tier-position button[data-position="RB"]')).to_have_attribute("aria-pressed", "true")
    # Sample data's current week is 1, so the default is rest of season.
    expect(page.locator('#tier-horizon button[data-horizon="ros"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".tier-name").first).to_have_text("Tier 1")
    expect(page.locator("#tier-status")).to_contain_text("for the rest of the season")
    gibbs = page.locator(".tier-player", has_text="Jahmyr Gibbs")
    expect(gibbs.locator(".tier-rank")).to_have_text("1")
    expect(gibbs.locator(".tier-player-name")).to_have_attribute("href", "/player/?id=9221")
    expect(gibbs.get_by_role("link", name="Trade for Jahmyr Gibbs")).to_have_attribute("href", "/#a=9221")
    expect(gibbs.get_by_role("link", name="Start/sit with Jahmyr Gibbs")).to_have_attribute(
        "href", "/start-sit/?mode=compare&p=9221")
    assert page.problems.errors == []


def test_switching_position_updates_the_list_and_url(page):
    open_tiers(page)
    page.click('#tier-position button[data-position="QB"]')
    expect(page).to_have_url(re.compile(r"pos=QB"))
    expect(page.locator(".tier-player.pos-QB").first).to_be_visible()
    expect(page.locator(".tier-player:not(.pos-QB)")).to_have_count(0)


def test_flex_mixes_positions_and_explains_the_value(page):
    open_tiers(page, "?pos=FLEX")
    expect(page.locator("#tier-note")).to_contain_text("replacement-level")
    for position in ("RB", "WR"):
        expect(page.locator(f".tier-player.pos-{position}").first).to_be_visible()
    expect(page.locator(".tier-player.pos-QB")).to_have_count(0)
    expect(page.locator(".tier-points").first).to_contain_text("+")


def test_url_settings_are_used(page):
    open_tiers(page, "?pos=WR&teams=10")
    expect(page.locator('#tier-position button[data-position="WR"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#tier-teams")).to_have_value("10")


def test_bad_url_settings_fall_back(page):
    open_tiers(page, "?pos=SUPERFLEX&teams=99&h=decade")
    expect(page.locator('#tier-position button[data-position="RB"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#tier-teams")).to_have_value("12")


def test_choosing_this_week_without_weekly_data_says_so(page):
    open_tiers(page)
    page.click('#tier-horizon button[data-horizon="week"]')
    expect(page.locator("#tier-list")).to_contain_text("No weekly projections for week 1")
    expect(page).to_have_url(re.compile(r"h=week"))


def test_in_season_default_falls_back_when_there_are_no_weekly_projections(page):
    app.dependency_overrides[get_matchups] = lambda: (MatchupData(), {"season": 2026, "weeks": [9, 17]})
    open_tiers(page)
    expect(page.locator('#tier-horizon button[data-horizon="ros"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#tier-note")).to_contain_text("no weekly projections yet")
    assert "h=" not in page.url  # the fallback isn't saved as a choice


def test_player_names_are_text_not_markup(page):
    open_tiers(page, "?pos=WR")
    expect(page.locator(".tier-player-name", has_text="<img")).to_have_count(1)
    assert page.evaluate("window.__pwned") is None


def test_fits_a_phone_screen(page):
    page.set_viewport_size({"width": 375, "height": 800})
    open_tiers(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
