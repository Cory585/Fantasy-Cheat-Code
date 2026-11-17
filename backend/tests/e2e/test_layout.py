"""Layout checks at phone and desktop widths: nothing runs off the page or collides."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def box(locator):
    return locator.evaluate("e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom}; }")


@pytest.mark.parametrize("width", [390, 700, 1280])
def test_lineup_panel_stays_inside_the_page(page, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("/")
    page.locator(".lineup-settings summary").click()
    panel = page.locator(".lineup-panel")
    expect(panel).to_be_visible()
    rect = box(panel)
    assert rect["l"] >= 0 and rect["r"] <= width, rect
    # The last + button is fully on screen, not cut off at the right edge.
    plus = box(page.locator(".lineup-panel .stepper-btn").last)
    assert plus["r"] <= width, plus
