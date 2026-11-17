import re

import pytest
from playwright.sync_api import expect

from app.api.dependencies import get_defense_breakdowns
from app.main import app
from app.services.defense_breakdown import DefenseBreakdowns
from tests.test_defense_breakdown import league_db  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.e2e


@pytest.fixture
def defenses(league_db):  # noqa: F811
    app.dependency_overrides[get_defense_breakdowns] = lambda: DefenseBreakdowns(league_db)


def test_team_breakdown_and_league_table(page, defenses):
    page.goto("/defense/?team=BUF")
    expect(page.locator("#df-team-name")).to_have_text("Buffalo Bills")
    expect(page.locator("#data-status")).to_have_text("2025 season through week 6.")
    cards = page.locator("#df-groups .df-card")
    expect(cards).to_have_count(4)
    expect(cards.first.locator(".df-card-rank-num")).to_have_text("1st")  # most sacks and pressure
    expect(page.locator("#df-units .df-card")).to_have_count(4)
    expect(page.locator("#df-allowed .df-allowed-tile")).to_have_count(4)
    expect(page.locator("#df-table-body tr")).to_have_count(4)

    # Sort the table by corners, then pick a team from it.
    page.click("button[data-key='CB']")
    expect(page.locator("#df-table-body tr").first.locator("th")).to_have_text("NYJ")
    page.click("#df-table-body button[data-team='NYJ']")
    expect(page.locator("#df-team-name")).to_have_text("NYJ")
    assert page.url.endswith("/defense/?team=NYJ")

    # The choice is remembered for the next visit.
    page.goto("/defense/")
    expect(page.locator("#team-select")).to_have_value("NYJ")
    assert not page.problems.errors


def test_switching_teams_dims_the_old_one_while_loading(page, defenses):
    page.goto("/defense/?team=BUF")
    expect(page.locator("#df-team-name")).to_have_text("Buffalo Bills")
    held = []
    page.route("**/defenses/NYJ", lambda route: held.append(route))
    page.select_option("#team-select", "NYJ")
    team = page.locator("#df-team")
    expect(team).to_have_attribute("aria-busy", "true")
    expect(team).to_have_class("is-stale")
    while not held:
        page.wait_for_timeout(50)
    held[0].continue_()
    expect(page.locator("#df-team-name")).to_have_text("NYJ")
    expect(team).to_have_attribute("aria-busy", "false")
    expect(team).not_to_have_class("is-stale")


def test_failed_switch_puts_the_select_back(page, defenses):
    page.goto("/defense/?team=BUF")
    expect(page.locator("#df-team-name")).to_have_text("Buffalo Bills")
    page.route("**/defenses/NYJ", lambda route: route.fulfill(
        status=500, content_type="application/json",
        body='{"detail": "Something went wrong on the server. Try again in a moment.", "code": "internal_error"}'))
    page.select_option("#team-select", "NYJ")
    error = page.locator("#df-error")
    expect(error).to_contain_text("Something went wrong on the server")
    # Buffalo is still on screen, so the select says Buffalo, not the team that failed.
    expect(page.locator("#team-select")).to_have_value("BUF")
    expect(page.locator("#df-team-name")).to_have_text("Buffalo Bills")
    page.unroute("**/defenses/NYJ")
    error.get_by_role("button", name="Try again").click()
    expect(page.locator("#df-team-name")).to_have_text("NYJ")
    expect(page.locator("#team-select")).to_have_value("NYJ")


def test_without_data_says_so(page):
    page.goto("/defense/")
    # The API's wording: the old developer instruction, or the newer "aren't available yet".
    expect(page.locator("#df-error")).to_contain_text(
        re.compile(r"python -m app\.data\.collect|Defense stats aren't available yet"))
    expect(page.locator("#df-team")).to_be_hidden()
