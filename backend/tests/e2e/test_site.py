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


NAV_GROUPS = ["Weekly", "Players", "Draft", "My leagues"]


@pytest.mark.parametrize("path", ["/", "/finder/", "/draft/", "/tiers/", "/start-sit/", "/team/", "/profile/",
                                  "/defense/"])
def test_nav_groups_tabs_and_marks_the_current_one(page, path):
    page.goto(path)
    expect(page.locator(".nav-group-label")).to_have_text(NAV_GROUPS)
    current = page.locator('.nav-links a[aria-current="page"]')
    expect(current).to_have_count(1)
    assert current.get_attribute("href") == path
    # Each group is a list named by its label, for screen readers.
    expect(page.get_by_role("list", name="Draft").get_by_role("link")).to_have_text(["Draft masterclass"])


DEVELOPER_TEXT = re.compile(r"python -m|Still to build|data collector|backend is running|data sync", re.IGNORECASE)


# Not /defense/: with no defense data, its error is the API's own message.
@pytest.mark.parametrize("path", PAGES)
def test_pages_show_no_developer_instructions(page, path):
    page.goto(path)
    page.wait_for_load_state("networkidle")
    text = page.locator("body").inner_text()
    assert not DEVELOPER_TEXT.search(text), DEVELOPER_TEXT.search(text).group(0)
