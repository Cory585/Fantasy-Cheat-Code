import json
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


def test_theme_choice_in_another_tab_wins_over_system_changes(page):
    page.emulate_media(color_scheme="light")
    page.goto("/")
    other = page.context.new_page()
    other.emulate_media(color_scheme="light")
    other.goto("/tiers/")
    assert other.evaluate("document.documentElement.dataset.theme") == "light"
    page.click("#theme-toggle")  # chosen in the first tab
    expect(other.locator("html")).to_have_attribute("data-theme", "dark")
    expect(other.locator(".theme-label")).to_have_text("Light mode")
    # The system flips to dark and back: the second tab keeps the saved choice.
    other.emulate_media(color_scheme="dark")
    other.emulate_media(color_scheme="light")
    expect(other.locator("html")).to_have_attribute("data-theme", "dark")
    other.close()


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


@pytest.mark.parametrize("path", [*PAGES, "/defense/"])
def test_pages_show_no_developer_instructions(page, path):
    page.goto(path)
    page.wait_for_load_state("networkidle")
    text = page.locator("body").inner_text()
    assert not DEVELOPER_TEXT.search(text), DEVELOPER_TEXT.search(text).group(0)


def fake_meta(page, source, **extra):
    meta = {"source": source, "players": 30, "outlooks": 0, "weeks": [1, 18], "current_week": 1, "matchups": False}
    if source == "synced":
        meta.update(season=2025, scoring="half_ppr", synced_at="2025-09-01T12:00:00Z")
    meta.update(extra)
    page.route("**/meta", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(meta)))


# Not /defense/: its data is collected separately from the player pool.
SAMPLE_PAGES = ["/", "/start-sit/", "/draft/", "/tiers/", "/tiers/?h=ros", "/finder/"]


@pytest.mark.parametrize("path", SAMPLE_PAGES)
def test_sample_data_is_flagged(page, path):
    fake_meta(page, "sample")
    page.goto(path)
    notice = page.locator(".masthead-title .data-notice")
    expect(notice).to_have_text("Showing sample players, not real projections.")
    expect(notice).to_have_attribute("role", "status")


def test_data_notice_is_a_live_region_before_meta_arrives(page):
    held = []
    page.route("**/meta", lambda route: held.append(route))
    page.goto("/finder/")
    notice = page.locator(".masthead-title .data-notice[role=status]")
    expect(notice).to_have_count(1)
    expect(notice).to_have_text("")
    while not held:
        page.wait_for_timeout(50)
    meta = {"source": "sample", "players": 12, "outlooks": 0, "weeks": [1, 18], "current_week": 1, "matchups": False}
    for route in held:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(meta))
    expect(notice).to_have_text("Showing sample players, not real projections.")


def test_defense_page_has_no_player_data_notice(page):
    fake_meta(page, "sample")
    page.goto("/defense/")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".data-notice")).to_have_count(0)


@pytest.mark.parametrize("path", ["/", "/start-sit/", "/draft/"])
def test_stale_synced_data_is_flagged(page, path):
    fake_meta(page, "synced", stale=True, synced_week=3, current_week=7)
    page.goto(path)
    expect(page.locator(".masthead-title .data-notice")).to_have_text(
        "Projections were last updated in week 3, so they may be out of date.")


@pytest.mark.parametrize("path", ["/", "/draft/", "/tiers/"])
def test_synced_data_is_not_flagged(page, path):
    fake_meta(page, "synced")
    page.goto(path)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".data-notice")).to_have_text("")


def test_404_page_links_every_section_in_nav_order(page):
    page.goto("/an/old/link")
    nav = page.locator(".nav-links a").evaluate_all("links => links.map(a => a.getAttribute(\"href\"))")
    links = page.locator(".not-found-links a").evaluate_all("links => links.map(a => a.getAttribute(\"href\"))")
    assert links == nav


@pytest.mark.parametrize("width", [260, 320, 375, 600, 800])
def test_every_nav_tab_is_visible_without_scrolling(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto("/finder/")
    links = page.locator(".nav-links a")
    expect(links).to_have_count(8)
    boxes = [links.nth(i).bounding_box() for i in range(8)]
    assert all(b["x"] >= 0 and b["x"] + b["width"] <= width for b in boxes), boxes
    assert page.evaluate("document.documentElement.scrollWidth") <= width


@pytest.mark.parametrize("path", ["/", "/start-sit/", "/tiers/", "/tiers/?h=ros", "/finder/", "/draft/"])
def test_pages_ask_for_meta_once(page, path):
    asked = []
    page.on("request", lambda request: request.url.endswith("/meta") and asked.append(request.url))
    page.goto(path)
    page.wait_for_load_state("networkidle")
    assert len(asked) == 1


def test_nav_masthead_and_main_share_one_page_width(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto("/")
    boxes = page.evaluate("""() => ['.site-nav-inner', '.masthead', 'main'].map(s => {
        const e = document.querySelector(s);
        return [getComputedStyle(e).maxWidth, e.getBoundingClientRect().left];
    })""")
    assert {width for width, _ in boxes} == {"1120px"}, boxes
    assert len({left for _, left in boxes}) == 1, boxes
