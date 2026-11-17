import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def open_draft(page, query="teams=12&slot=5&rounds=15"):
    page.goto(f"/draft/?{query}")
    expect(page.locator(".plan-pick")).to_have_count(15 if "rounds=15" in query else int(re.search(r"rounds=(\d+)", query).group(1)))


def test_loads_a_plan_for_the_url_settings(page):
    open_draft(page)
    expect(page.locator("#draft-slot")).to_have_value("5")
    expect(page.locator('#league-size button[data-teams="12"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".plan-pick-head strong").first).to_have_text("1.05")
    expect(page.locator("#data-status")).to_contain_text("Plan for the 5th pick")
    assert page.problems.errors == []


def test_league_size_buttons_resize_the_draft(page):
    open_draft(page)
    page.click('#league-size button[data-teams="8"]')
    expect(page.locator("#draft-slot option")).to_have_count(8)
    expect(page.locator(".team-head")).to_have_count(8)
    expect(page).to_have_url(re.compile(r"teams=8"))
    expect(page.locator(".plan-pick-head strong").nth(1)).to_have_text("2.04")  # 8 teams, slot 5


def test_slot_clamps_when_the_league_shrinks(page):
    open_draft(page, "teams=16&slot=16&rounds=15")
    page.click('#league-size button[data-teams="8"]')
    expect(page.locator("#draft-slot")).to_have_value("8")


def test_stepper_moves_focus_to_the_select_at_the_end(page):
    open_draft(page, "teams=8&slot=7&rounds=15")
    page.focus("#slot-next")
    page.keyboard.press("Enter")
    expect(page.locator("#draft-slot")).to_have_value("8")
    expect(page.locator("#slot-next")).to_be_disabled()
    assert page.evaluate("document.activeElement.id") == "draft-slot"


def test_choosing_a_team_from_the_board_keeps_keyboard_focus(page):
    open_draft(page)
    page.focus('.team-head[data-slot="3"]')
    page.keyboard.press("Enter")
    expect(page.locator("#draft-slot")).to_have_value("3")
    expect(page.locator(".plan-pick-head strong").first).to_have_text("1.03")
    assert page.evaluate("document.activeElement.dataset.slot") == "3"
    expect(page.locator(".team-head.is-mine")).to_have_attribute("data-slot", "3")


def test_position_filter_buttons(page):
    open_draft(page)
    page.click('#position-filter button[data-position="RB"]')
    expect(page.locator('#position-filter button[data-position="RB"]')).to_have_attribute("aria-pressed", "true")
    tags = page.locator(".target .pos-tag").all_inner_texts()
    assert tags and set(tags) == {"RB"}
    expect(page).to_have_url(re.compile(r"pos=RB"))


def test_cards_for_old_settings_never_reappear(page):
    open_draft(page)
    calls = {"n": 0}

    def fail_after_first(route):
        calls["n"] += 1
        route.fulfill(status=500, content_type="application/json", body='{"detail": "x", "code": "internal_error"}')

    page.route("**/draft/plan?*", fail_after_first)
    page.select_option("#draft-slot", "9")
    expect(page.locator(".error-block")).to_be_visible()
    page.click('#position-filter button[data-position="QB"]')
    expect(page.locator(".plan-pick")).to_have_count(0)
    assert calls["n"] == 1


def test_unreadable_response_shows_an_error(page):
    open_draft(page)
    page.route("**/draft/plan?*", lambda route: route.fulfill(status=200, content_type="application/json", body="<html>proxy</html>"))
    page.select_option("#draft-slot", "2")
    expect(page.locator(".error-block")).to_contain_text("couldn't read")


def test_timeline_dots_are_focusable_and_labeled(page):
    open_draft(page)
    dots = page.locator(".my-pick-dot")
    expect(dots).to_have_count(15)
    first = dots.first
    expect(first).to_have_attribute("tabindex", "0")
    expect(first).to_have_attribute("aria-label", re.compile(r"1\.05: round 1, 5th overall\. 14 picks until your next\."))
    first.focus()
    expect(page.locator("#viz-tooltip")).to_be_visible()


def test_board_has_table_semantics_and_profile_links(page):
    open_draft(page)
    expect(page.locator('.draft-grid[role="table"]')).to_have_count(1)
    expect(page.locator('.draft-grid [role="row"]')).to_have_count(16)  # header + 15 rounds
    expect(page.locator('[role="columnheader"]')).to_have_count(13)  # corner + 12 teams
    link = page.locator(".pick-name.profile-link").first
    expect(link).to_have_attribute("href", re.compile(r"^/player/\?id="))


def test_hovering_a_board_cell_shows_a_tooltip(page):
    open_draft(page)
    page.hover(".pick.pos-QB >> nth=0")
    expect(page.locator("#viz-tooltip")).to_contain_text("QB")


def test_names_with_markup_render_as_text(page):
    open_draft(page, "teams=4&slot=1&rounds=18")
    assert page.evaluate("window.__pwned") is None
    expect(page.locator("main img")).to_have_count(0)


def test_narrow_screens_do_not_scroll_sideways(page):
    page.set_viewport_size({"width": 360, "height": 800})
    open_draft(page)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0
