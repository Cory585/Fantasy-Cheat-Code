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


def overlaps(a, b):
    return a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]


@pytest.mark.parametrize("path", ["/", "/team/", "/draft/", "/defense/"])
@pytest.mark.parametrize("width", [1280, 900, 760, 700, 520])
def test_star_portrait_clears_the_nav_title_and_controls(page, path, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(path)
    portrait = page.locator(".star-portrait")
    expect(portrait).to_be_attached()
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('.star-portrait')).getPropertyValue('--nav-height') !== ''")
    art = box(portrait)
    nav = box(page.locator(".site-nav"))
    # Wholly below the nav at every width, so the nav doesn't cut off the top of the head (#74).
    assert art["t"] >= nav["b"], (art, nav)
    for el in page.locator(".masthead-title > *, .masthead-controls > *").all():
        if el.is_visible():
            assert not overlaps(art, box(el)), (el.evaluate("e => e.outerHTML.slice(0, 80)"), art, box(el))


def test_star_portrait_is_left_out_on_small_phones(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/")
    expect(page.locator(".star-portrait")).to_be_hidden()


def test_small_controls_have_24px_targets(page):
    page.goto("/")
    page.locator(".lineup-settings summary").click()
    step = box(page.locator(".lineup-panel .stepper-btn").first)
    assert step["r"] - step["l"] >= 24 and step["b"] - step["t"] >= 24, step

    page.goto("/tiers/")
    link = page.get_by_role("link", name="Trade for Jahmyr Gibbs")
    rect = box(link)
    assert rect["r"] - rect["l"] >= 24 and rect["b"] - rect["t"] >= 24, rect

    page.goto("/draft/?teams=12&slot=5&rounds=15")
    dot = page.locator(".my-pick-dot").first
    dot.scroll_into_view_if_needed()
    rect = box(dot)
    cx, cy = (rect["l"] + rect["r"]) / 2, (rect["t"] + rect["b"]) / 2
    # A point 11px from the dot's center still finds its tooltip.
    tip = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('[data-tip]')?.dataset.tip", [cx + 11, cy])
    assert tip == dot.get_attribute("data-tip")


NAV_PAGES = ["/", "/start-sit/", "/defense/", "/tiers/", "/finder/", "/draft/", "/team/", "/profile/"]


@pytest.mark.parametrize("width", [1280, 390])
def test_nav_tabs_stay_put_from_page_to_page(page, width):
    # The current tab is bolder; it mustn't widen and push the other tabs around (#72).
    page.set_viewport_size({"width": width, "height": 900})
    layouts = {}
    for path in NAV_PAGES:
        page.goto(path)
        # The web font is blocked in tests, and the fallback may draw 500 and 600 alike; Arial's
        # bold is clearly wider, so the check means something.
        page.add_style_tag(content=".nav-links a { font-family: Arial, sans-serif; }")
        layouts[path] = page.locator(".site-nav a").evaluate_all(
            "links => links.map(a => { const r = a.getBoundingClientRect(); return [r.left, r.top, r.width]; })")
    first = layouts[NAV_PAGES[0]]
    for path, layout in layouts.items():
        assert layout == first, (path, layout, first)
