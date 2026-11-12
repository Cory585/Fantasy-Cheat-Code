import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e

KEY = "fcc.team.v1"
ROSTER = ["qb1", "qb2", "rb1", "rb2", "rb3", "wr1", "wr2", "wr3", "te1", "te2", "k1", "dst1"]


def saved(page):
    return json.loads(page.evaluate(f"localStorage.getItem('{KEY}')") or "null")


def seed(page, roster=ROSTER, **extra):
    team = {"name": "Test Squad", "roster": roster, **extra}
    page.add_init_script(f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(team))})")


def test_building_a_team_saves_it(page):
    page.goto("/team/")
    expect(page.locator(".roster-empty")).to_be_visible()
    page.fill("#team-name-input", "Gridiron Gang")
    search = page.locator("#team-search")
    for name in ("Sample QB One", "Sample RB One", "Sample WR Two"):
        search.fill(name)
        search.press("Enter")
    expect(page.locator(".team-item")).to_have_count(3)
    expect(page.locator("#roster-count")).to_have_text("3 of 40 players")
    page.click('button[aria-label="More SUPERFLEX spots"]')
    team = saved(page)
    assert team["name"] == "Gridiron Gang"
    assert team["roster"] == ["qb1", "rb1", "wr2"]
    assert team["slots"]["SUPERFLEX"] == 1
    # Still there after a reload.
    page.reload()
    expect(page.locator(".team-item")).to_have_count(3)
    expect(page.locator("#team-name-input")).to_have_value("Gridiron Gang")
    assert page.problems.errors == []


def test_roster_is_grouped_and_removable(page):
    seed(page)
    page.goto("/team/")
    expect(page.locator(".team-group")).to_have_count(6)
    page.click('button[aria-label="Remove Sample QB Two"]')
    expect(page.locator(".team-item")).to_have_count(len(ROSTER) - 1)
    assert "qb2" not in saved(page)["roster"]


def test_stepper_keeps_focus_and_limits(page):
    page.goto("/team/")
    minus = page.locator('button[aria-label="Fewer QB spots"]')
    minus.click()
    expect(minus).to_be_disabled()
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "More QB spots"


def test_clear_needs_two_clicks(page):
    seed(page)
    page.goto("/team/")
    page.click("#clear-team")
    expect(page.locator("#clear-team")).to_have_text("Click again to clear")
    assert saved(page)["roster"]
    page.click("#clear-team")
    expect(page.locator(".roster-empty")).to_be_visible()
    assert saved(page) is None


def test_bad_saved_data_is_ignored(page):
    page.add_init_script(f"localStorage.setItem('{KEY}', '{{not json')")
    page.goto("/team/")
    expect(page.locator(".roster-empty")).to_be_visible()
    assert page.problems.errors == []


def test_lineup_mode_uses_the_saved_team(page):
    seed(page)
    page.goto("/start-sit/")
    expect(page.locator('#ss-mode button[data-mode="lineup"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".ss-lineup-head h2")).to_have_text("Test Squad: week 1 lineup")
    expect(page.locator(".ss-spot")).to_have_count(9)
    expect(page.locator(".ss-spot").first).to_contain_text("Sample QB One")
    expect(page.locator(".ss-bench-item")).to_have_count(3)
    expect(page.locator("#ss-search")).to_be_hidden()
    expect(page).to_have_url(re.compile("mode=lineup"))
    assert page.problems.errors == []


def test_lineup_follows_the_week_and_byes(page):
    seed(page)
    page.goto("/start-sit/?mode=lineup&week=6")
    # Sample WR One (MIN) is on bye in week 6, so the other two WRs start.
    expect(page.locator(".ss-lineup-head h2")).to_contain_text("week 6")
    expect(page.locator(".ss-bench-item", has_text="Sample WR One")).to_contain_text("Bye")


def test_close_call_opens_a_comparison(page):
    seed(page)
    page.goto("/start-sit/?mode=lineup&week=2")
    expect(page.locator(".ss-spot")).to_have_count(9)
    button = page.locator(".ss-close button").first
    if button.count() == 0:
        pytest.skip("no close calls in this sample week")
    button.click()
    expect(page.locator('#ss-mode button[data-mode="compare"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".ss-chip")).to_have_count(2)
    expect(page.locator(".ss-verdict")).to_be_visible()


def test_compare_mode_offers_players_from_the_team(page):
    seed(page)
    page.goto("/start-sit/?mode=compare")
    expect(page.locator(".ss-quick-btn")).to_have_count(len(ROSTER))
    page.click('.ss-quick-btn:text-is("Sample WR One")')
    # Only players who can share a spot with a WR are offered now.
    expect(page.locator(".ss-quick-btn")).to_have_count(9)
    page.click('.ss-quick-btn:text-is("Sample WR Two")')
    expect(page.locator(".ss-verdict")).to_be_visible()


def test_without_a_team_lineup_mode_points_to_the_team_page(page):
    page.goto("/start-sit/?mode=lineup")
    link = page.locator(".ss-no-team a")
    expect(link).to_have_attribute("href", "/team/")
    page.goto("/start-sit/")
    expect(page.locator('#ss-mode button[data-mode="compare"]')).to_have_attribute("aria-pressed", "true")


def test_players_no_longer_in_the_pool_are_skipped(page):
    seed(page, roster=ROSTER + ["gone1"])
    page.goto("/start-sit/?mode=lineup")
    expect(page.locator(".warnings")).to_contain_text("no longer in the player pool")
    page.goto("/team/")
    expect(page.locator("#roster-count")).to_contain_text("1 no longer in the player pool")


def test_team_player_names_render_as_text(page):
    seed(page, roster=["666", "wr1"])
    page.goto("/team/")
    expect(page.locator(".team-item", has_text=XSS_NAME)).to_have_count(1)
    page.goto("/start-sit/?mode=lineup")
    expect(page.locator(".ss-spot", has_text=XSS_NAME)).to_have_count(1)
    assert page.evaluate("window.__pwned") is None


def test_team_page_fits_a_phone(page):
    seed(page)
    page.set_viewport_size({"width": 375, "height": 800})
    for path in ("/team/", "/start-sit/?mode=lineup"):
        page.goto(path)
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth") <= 375, path
