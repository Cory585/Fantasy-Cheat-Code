import re

import pytest
from playwright.sync_api import expect

from app.api.dependencies import get_stat_tags
from app.main import app
from app.models.tags import PlayerTag
from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e

TAGS = {
    "wr1": [PlayerTag(key="slot", detail="38% of targets between the numbers, 7.2-yard average depth"),
            PlayerTag(key="high_target_share", detail="26% of team targets")],
    "wr2": [PlayerTag(key="outside", detail="88% of targets outside the numbers, 12.4-yard average depth"),
            PlayerTag(key="high_target_share", detail="23% of team targets")],
    "wr3": [PlayerTag(key="slot", detail="30% of targets between the numbers, 9.0-yard average depth")],
    "rb1": [PlayerTag(key="workhorse", detail="68% of team carries")],
    "9221": [PlayerTag(key="pass_catching_back", detail="5.1 targets a game")],
    "666": [PlayerTag(key="slot", detail="40% inside")],
}


@pytest.fixture(autouse=True)
def fake_tags():
    app.dependency_overrides[get_stat_tags] = lambda: (TAGS, 2026)
    yield


def names(page):
    return page.locator(".finder-item .ss-chip-name").all_inner_texts()


def test_tags_filter_players(page):
    page.goto("/finder/")
    expect(page.locator("#finder-status")).to_contain_text("2026 season")
    page.click('#finder-pos button[data-pos="WR"]')
    expect(page.locator('button[data-tag="workhorse"]')).to_have_count(0)  # not a WR tag
    slot = page.locator('button[data-tag="slot"]')
    expect(slot.locator(".tag-chip-count")).to_have_text("3")
    slot.click()
    expect(slot).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".finder-item")).to_have_count(3)
    assert set(names(page)) == {"Sample WR One", "Sample WR Three", XSS_NAME}
    expect(page).to_have_url(re.compile(r"tag=slot&pos=WR"))
    # Two tags: everyone must have both, or either with Any.
    page.click('button[data-tag="high_target_share"]')
    expect(page.locator(".finder-item")).to_have_count(1)
    expect(page.locator(".finder-item")).to_contain_text("Sample WR One")
    page.click('#finder-match button[data-match="any"]')
    expect(page.locator(".finder-item")).to_have_count(4)
    assert page.evaluate("window.__pwned") is None
    assert page.problems.errors == []


def test_links_reopen_a_search(page):
    page.goto("/finder/?tag=outside")
    expect(page.locator(".finder-item")).to_have_count(1)
    pill = page.locator(".finder-item .tag-pill", has_text="Outside receiver")
    expect(pill).to_have_class(re.compile("is-on"))
    expect(pill).to_have_attribute("title", re.compile("12.4-yard"))


def test_name_filter_and_clear(page):
    page.goto("/finder/?tag=slot")
    page.fill("#finder-q", "three")
    expect(page.locator(".finder-item")).to_have_count(1)
    page.fill("#finder-q", "")
    page.click("#finder-clear")
    expect(page.locator('button[data-tag="slot"]')).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#results-note")).to_contain_text("for all players")


def test_profile_shows_tags_that_link_to_the_finder(page):
    page.goto("/player/?id=9221")
    link = page.locator("#profile-tags a")
    expect(link).to_have_text("Pass-catching back")
    expect(link).to_have_attribute("href", "/finder/?tag=pass_catching_back&pos=RB")
    link.click()
    expect(page.locator(".finder-item")).to_contain_text("Jahmyr Gibbs")


def test_finder_fits_a_phone(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("/finder/?tag=slot")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
