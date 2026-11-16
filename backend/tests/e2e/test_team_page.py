import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import XSS_NAME

pytestmark = pytest.mark.e2e

KEY = "fcc.team.v1"  # where the single-team version kept the team; moved into a league on load
ROSTER = ["qb1", "qb2", "rb1", "rb2", "rb3", "wr1", "wr2", "wr3", "te1", "te2", "k1", "dst1"]


def saved(page):
    """The first league on the page's profile, once any pending save has gone out."""
    expect(page.locator("#save-status")).not_to_have_text("Saving…")
    leagues = page.evaluate("""async () => {
        const r = await fetch('/leagues', {headers: {'X-Profile-Key': localStorage.getItem('fcc.profile.v1')}});
        return (await r.json()).leagues;
    }""")
    return leagues[0] if leagues else None


def seed(page, roster=ROSTER, **extra):
    """Saves a team the way the old single-team version did, so loading a page moves it into a league."""
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
    league = saved(page)
    assert league["roster"] == [] and league["name"] == "Test Squad"  # the league stays


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


def test_lineup_mentions_players_no_longer_in_the_pool(page):
    seed(page, ROSTER + ["gone1", "gone2"])
    page.goto("/start-sit/?mode=lineup&week=2")
    expect(page.locator(".ss-missing")).to_have_text(
        "2 players on this roster are no longer in the player pool, so they're left out of the lineup.")
    expect(page.locator(".ss-spot")).to_have_count(9)


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


def test_old_saved_team_moves_into_a_league(page):
    seed(page)
    page.goto("/team/")
    expect(page.locator(".team-item")).to_have_count(len(ROSTER))
    expect(page.locator("#team-name-input")).to_have_value("Test Squad")
    assert page.evaluate(f"localStorage.getItem('{KEY}')") is None
    assert saved(page)["roster"] == ROSTER
    # Reloading doesn't make a second copy.
    page.reload()
    expect(page.locator(".team-item")).to_have_count(len(ROSTER))
    count = page.evaluate("""async () => (await (await fetch('/leagues',
        {headers: {'X-Profile-Key': localStorage.getItem('fcc.profile.v1')}})).json()).leagues.length""")
    assert count == 1


def test_search_waits_for_the_player_list(page):
    # Hold the player list back, as a slow connection would: the search stays disabled until
    # it arrives, so a name typed and entered straight away still gets added.
    release = []
    page.route("**/players", lambda route: release.append(route))
    page.goto("/team/")
    search = page.locator("#team-search")
    expect(search).to_be_disabled()
    expect(search).to_have_attribute("placeholder", "Loading players…")
    while not release:
        page.wait_for_timeout(50)
    # The page's backdrop asks for /players too; let every held request through.
    for route in release:
        route.continue_()
    page.unroute("**/players")
    search.fill("Sample RB One")
    search.press("Enter")
    expect(page.locator(".team-item")).to_have_count(1)
    expect(search).to_have_attribute("placeholder", "Search players or teams to add")
