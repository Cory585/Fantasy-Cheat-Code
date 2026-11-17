import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

LENIS = re.compile(r"\blenis\b")  # Lenis marks the root element while it smooths scrolling


def test_reduced_motion_turned_on_mid_visit_stops_smooth_scrolling(page):
    page.emulate_media(reduced_motion="no-preference")
    page.goto("/")
    html = page.locator("html")
    expect(html).to_have_class(LENIS)
    page.emulate_media(reduced_motion="reduce")
    expect(html).not_to_have_class(LENIS)
    page.emulate_media(reduced_motion="no-preference")
    expect(html).to_have_class(LENIS)


def test_reduced_motion_turned_on_mid_visit_skips_animations(page):
    page.emulate_media(reduced_motion="no-preference")
    page.goto("/")
    page.emulate_media(reduced_motion="reduce")
    page.locator(".lineup-settings summary").click()
    # The panel is at full opacity straight away, with no fade in.
    assert page.locator(".lineup-panel").evaluate("e => getComputedStyle(e).opacity") == "1"
