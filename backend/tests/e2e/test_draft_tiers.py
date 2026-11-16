"""Tier chips and "running dry" warnings on the draft page."""

import pytest
from playwright.sync_api import expect

from app.api.dependencies import get_player_repository
from app.data.repository import InMemoryPlayerRepository
from app.main import app
from app.models.player import Player, Position

pytestmark = pytest.mark.e2e

# Three tier-1 RBs at the top of the board, a clear drop to two more, and a smooth run of WRs.
POOL = (
    [Player(id=f"rb{i}", name=f"Running Back {i}", position=Position.RB, team="KC", ros_projection=300 - i)
     for i in range(3)]
    + [Player(id=f"rb{i}", name=f"Running Back {i}", position=Position.RB, team="KC", ros_projection=150 - i)
       for i in range(3, 5)]
    + [Player(id=f"wr{i}", name=f"Wide Receiver {i}", position=Position.WR, team="BUF", ros_projection=200 - i)
       for i in range(16)]
)


@pytest.fixture
def pool():
    app.dependency_overrides[get_player_repository] = lambda: InMemoryPlayerRepository(POOL)


def open_plan(page, query="teams=8&slot=1&rounds=13"):
    page.goto(f"/draft/?{query}")
    expect(page.locator(".plan-pick")).to_have_count(13)


def test_first_pick_warns_that_the_top_running_backs_will_be_gone(page, pool):
    open_plan(page)
    first = page.locator(".plan-pick").first
    # The RB warning comes first: every one of them is gone, where some tier-1 WRs might not be.
    alert = first.locator(".tier-alert").first
    expect(alert).to_have_class("tier-alert pos-RB is-last")
    expect(alert).to_contain_text("Last chance: all 3 Tier 1 RBs left are likely gone before 2.08")
    # The targets show their tier, and the tier running dry is marked.
    chip = first.locator(".target", has_text="Running Back 0").locator(".tier-chip")
    expect(chip).to_have_text("T1")
    expect(chip).to_have_class("tier-chip is-dry")
    assert page.problems.errors == []


def test_position_filter_hides_other_positions_alerts(page, pool):
    open_plan(page)
    alerts = page.locator(".plan-pick").first.locator(".tier-alert")
    page.click('#position-filter button[data-position="WR"]')
    expect(page.locator(".plan-pick").first.locator(".tier-alert.pos-RB")).to_have_count(0)
    expect(page.locator(".plan-pick").first.locator(".tier-alert.pos-WR")).to_have_count(1)
    page.click('#position-filter button[data-position="RB"]')
    expect(alerts).to_have_count(1)
    expect(alerts).to_have_class("tier-alert pos-RB is-last")


def test_board_tooltips_name_the_tier(page, pool):
    open_plan(page)
    cell = page.locator(".pick", has_text="R. Back 0").first
    assert "RB tier 1" in cell.get_attribute("data-tip")
