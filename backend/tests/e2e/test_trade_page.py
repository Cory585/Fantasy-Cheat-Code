import json
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_trade_explains_both_sides_and_breaks_down_each_player(page):
    page.goto("/#a=rb1&b=wr2,rb3")
    why = page.locator(".tr-why-side")
    expect(why).to_have_count(2)
    expect(why.nth(0).locator("h3")).to_have_text("Why Team A wins")
    expect(why.nth(1).locator("h3")).to_have_text("Why Team B loses")
    first = why.nth(0).locator(".tr-reason").first
    expect(first).to_have_class("tr-reason is-plus")
    expect(first).to_contain_text("Receives 268 points of value for 180 sent (+88)")
    expect(why.nth(1)).to_contain_text("Gets the best player in the deal: Sample RB One")
    expect(why.nth(1)).to_contain_text("Consolidates 2 players into 1")

    cards = page.locator(".tr-player")
    expect(cards).to_have_count(3)
    side_a_card = page.locator(".tr-breakdown-a .tr-player")
    expect(side_a_card.locator(".tr-player-name")).to_have_text("Sample RB One")
    expect(side_a_card.locator(".tr-tier")).to_have_text("RB1")
    expect(side_a_card.locator(".tr-traits-plus")).to_contain_text("Top-3 RB")
    injured = page.locator(".tr-breakdown-b .tr-player", has_text="Sample RB Three")
    expect(injured.locator(".tr-traits-minus")).to_contain_text("Listed Q: trade value cut to 90%")
    # Cards are sorted by value, and each shows its share of the side.
    expect(page.locator(".tr-breakdown-b .tr-player-name").first).to_have_text("Sample WR Two")
    expect(page.locator(".tr-breakdown-b .tr-player").first).to_contain_text("60%")  # 160 of 268
    assert not page.problems.errors


def test_even_trade_says_so(page):
    page.goto("/#a=qb1&b=qb2")
    expect(page.locator(".tr-why-side h3").first).to_have_text("Team A: an even deal")


def test_searches_wait_for_the_player_list(page):
    # Hold the player list back, as a slow connection would: both searches stay disabled
    # until it arrives, so nothing typed is lost.
    release = []
    page.route("**/players", lambda route: release.append(route))
    page.goto("/")
    searches = page.locator(".player-search")
    for i in range(2):
        expect(searches.nth(i)).to_be_disabled()
        expect(searches.nth(i)).to_have_attribute("placeholder", "Loading players…")
    expect(page.locator("#result")).to_have_text("Loading players…")
    expect(page.locator("#data-status")).to_have_attribute("role", "status")
    while not release:
        page.wait_for_timeout(50)
    # The page's backdrop asks for /players too; let every held request through.
    for route in release:
        route.continue_()
    page.unroute("**/players")
    search = page.locator('.side[data-side="a"] .player-search')
    expect(search).to_be_enabled()
    search.fill("Sample RB One")
    search.press("Enter")
    expect(page.locator('.side[data-side="a"] .roster-item')).to_have_count(1)


def test_position_filter_is_a_segmented_control(page):
    page.goto("/")
    side = page.locator('.side[data-side="a"]')
    expect(side.locator(".player-search")).to_be_enabled()
    side.get_by_role("group", name="Filter Team A search by position").get_by_role("button", name="RB").click()
    expect(side.locator('.segmented button[data-pos="RB"]')).to_have_attribute("aria-pressed", "true")
    expect(side.locator('.segmented button[data-pos=""]')).to_have_attribute("aria-pressed", "false")
    metas = side.locator(".search-option-meta")
    expect(metas.first).to_be_visible()
    for text in metas.all_inner_texts():
        assert text.startswith("RB")


def test_share_link_copies_without_a_prompt(page):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto("/#a=rb1&b=wr2")
    expect(page.locator(".tr-why-side")).to_have_count(2)
    # Block the Clipboard API, as an insecure context would: the copy falls back instead of prompting.
    page.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error(\"blocked\")); }")
    page.click("#share")
    expect(page.locator("#share-status")).to_have_text(re.compile(r"Link copied|Copy the address bar to share"))
    assert dialogs == []


def test_search_adds_players_with_the_keyboard(page):
    page.goto("/")
    search = page.locator('.side[data-side="b"] .player-search')
    expect(search).to_be_enabled()
    search.fill("Sample WR")
    # Typing highlights the first match, so Enter has a visible, announced choice.
    first = page.locator("#results-b .search-option").first
    expect(first).to_have_attribute("aria-selected", "true")
    expect(search).to_have_attribute("aria-activedescendant", "results-b-opt-0")
    search.press("ArrowDown")
    expect(search).to_have_attribute("aria-activedescendant", "results-b-opt-1")
    second = page.locator("#results-b .search-option-name").nth(1).inner_text()
    search.press("Enter")
    expect(page.locator('.side[data-side="b"] .roster-item')).to_have_count(1)
    expect(page.locator('.side[data-side="b"] .roster-item')).to_contain_text(second)
    expect(page.locator("#results-b")).to_be_hidden()
    assert "b=" in page.url


def test_enter_in_an_empty_search_adds_nobody(page):
    page.goto("/")
    search = page.locator('.side[data-side="a"] .player-search')
    expect(search).to_be_enabled()
    search.click()
    search.press("Enter")
    search.fill("Sample WR")
    search.fill("")  # typed, then cleared
    search.press("Enter")
    expect(page.locator('.side[data-side="a"] .roster-item')).to_have_count(0)
    # With an option picked from the list, Enter still adds it.
    search.press("ArrowDown")
    search.press("Enter")
    expect(page.locator('.side[data-side="a"] .roster-item')).to_have_count(1)


def test_past_byes_drop_off_once_the_current_week_is_known(page):
    held = []
    page.route("**/meta", lambda route: held.append(route))
    page.goto("/#a=rb3&b=wr2")
    rb3 = page.locator(".roster-item", has_text="Sample RB Three").locator(".player-meta")
    expect(rb3).to_contain_text("bye week 5")  # before /meta, every bye is shown
    while not held:
        page.wait_for_timeout(50)
    meta = {"source": "sample", "players": 12, "outlooks": 0, "weeks": [1, 18], "current_week": 7, "matchups": False}
    for route in held:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(meta))
    expect(rb3).not_to_contain_text("bye week")
    expect(page.locator(".roster-item", has_text="Sample WR Two")).to_contain_text("bye week 10")
