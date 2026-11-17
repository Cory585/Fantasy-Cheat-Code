"""Screen reader and keyboard checks: skip link, headings, status regions, errors, focus."""

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
