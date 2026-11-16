import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

PAGES = ["/", "/finder/", "/draft/", "/tiers/", "/start-sit/", "/team/", "/profile/", "/player/?id=9221"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_load_cleanly_in_both_themes(page, path):
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme)
        page.goto(path)
        page.wait_for_load_state("networkidle")
        expected = "Light mode" if theme == "dark" else "Dark mode"
        expect(page.locator(".theme-label")).to_have_text(expected)
    assert page.problems.errors == []


def test_theme_toggle_persists_across_pages(page):
    page.emulate_media(color_scheme="light")
    page.goto("/")
    page.click("#theme-toggle")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.goto("/draft/")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"


def test_missing_page_shows_the_404_page(page):
    response = page.goto("/an/old/link")
    assert response.status == 404
    expect(page.locator("h1")).to_have_text("Page not found")
    page.click("text=Draft masterclass >> nth=-1")
    # The draft page puts its settings in the URL as soon as it loads, so allow a query.
    expect(page).to_have_url(re.compile(r"/draft/(\?.*)?$"))
    expect(page.locator("h1")).to_have_text("Draft Masterclass")


def test_security_headers_reach_the_browser(page):
    response = page.goto("/")
    headers = response.headers
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
