"""Failed requests: every page shows the same error block, worded for people, with a working
"Try again" button (PlayerUI.errorBlock in player-search.js)."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class Failing:
    """Answer requests matching `pattern` with `response` (or abort them when it's empty)
    until `.stop()`, then let them through to the real server."""

    def __init__(self, page, pattern, **response):
        self.on, self.response = True, response
        page.route(pattern, self.handle)

    def handle(self, route):
        if not self.on:
            return route.continue_()
        if not self.response:
            return route.abort()
        return route.fulfill(**self.response)

    def stop(self):
        self.on = False


SERVER_ERROR = {"status": 500, "content_type": "application/json",
                "body": '{"detail": "Something went wrong on the server. Try again in a moment.", "code": "internal_error"}'}


def test_trade_page_retries_loading_players(page):
    failing = Failing(page, "**/players", **SERVER_ERROR)
    page.goto("/#a=rb1&b=wr2")
    block = page.locator("#result .error-block")
    expect(block).to_contain_text("Something went wrong on the server")
    failing.stop()
    block.get_by_role("button", name="Try again").click()
    expect(page.locator(".tr-why-side")).to_have_count(2)


def test_trade_analysis_error_reads_the_validation_detail(page):
    page.route("**/trades/analyze", lambda route: route.fulfill(
        status=422, content_type="application/json",
        body='{"detail": [{"msg": "List should have at most 40 items"}], "code": "invalid_request"}'))
    page.goto("/#a=rb1&b=wr2")
    expect(page.locator("#result .error-block")).to_contain_text("List should have at most 40 items")


def test_tiers_offline_then_retry(page):
    failing = Failing(page, "**/tiers?*")
    page.goto("/tiers/")
    block = page.locator("#tier-list .error-block")
    expect(block).to_contain_text("Couldn't reach the server. Check your connection")
    failing.stop()
    block.get_by_role("button", name="Try again").click()
    expect(page.locator(".tier-row").first).to_be_visible()


def test_rate_limit_counts_down_before_retry(page):
    failing = Failing(page, "**/tiers?*", status=429, headers={"Retry-After": "2"}, content_type="application/json",
                      body='{"detail": "Too many requests", "code": "rate_limited"}')
    page.goto("/tiers/")
    block = page.locator("#tier-list .error-block")
    expect(block).to_contain_text("Too many requests just now. Try again in 2 seconds.")
    retry = block.get_by_role("button")
    expect(retry).to_be_disabled()
    expect(retry).to_have_text("Try again in 2s")
    expect(retry).to_have_text("Try again", timeout=4000)
    failing.stop()
    retry.click()
    expect(page.locator(".tier-row").first).to_be_visible()


def test_draft_plan_error_has_a_retry(page):
    failing = Failing(page, "**/draft/plan?*", **SERVER_ERROR)
    page.goto("/draft/")
    block = page.locator("#plan-list .error-block")
    expect(block).to_be_visible()
    failing.stop()
    block.get_by_role("button", name="Try again").click()
    expect(page.locator(".plan-pick").first).to_be_visible()


def test_finder_retries_loading(page):
    failing = Failing(page, "**/tags", **SERVER_ERROR)
    page.goto("/finder/")
    block = page.locator("#finder-list .error-block")
    expect(block).to_be_visible()
    failing.stop()
    block.get_by_role("button", name="Try again").click()
    expect(page.locator(".finder-item").first).to_be_visible()


def test_defense_error_has_a_retry(page):
    page.goto("/defense/")
    expect(page.locator("#df-error .error-block").get_by_role("button", name="Try again")).to_be_visible()
