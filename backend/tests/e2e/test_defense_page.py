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


def test_without_data_says_stats_are_not_available_yet(page):
    page.goto("/defense/")
    expect(page.locator("#df-error")).to_contain_text("Defense stats aren't available yet")
    expect(page.locator("#df-error")).not_to_contain_text("python -m")
    expect(page.locator("#df-team")).to_be_hidden()
