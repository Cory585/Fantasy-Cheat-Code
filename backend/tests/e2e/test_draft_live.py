"""The draft page following an ESPN draft live, against a scripted stand-in for ESPN."""

import re
import threading

import pytest
from playwright.sync_api import expect

from app.api.dependencies import get_live_drafts
from app.data.sources.espn_draft import EspnDraft, EspnDraftPick, EspnDraftTeam, LeaguePrivate
from app.main import app
from app.services.live_draft import LiveDrafts

pytestmark = pytest.mark.e2e

S2 = "AEBx" * 10
SWID = "{ABCDEF12-3456-7890-ABCD-EF1234567890}"
TEAMS = 8
# ESPN ids in the e2e pool: Jahmyr Gibbs, a player whose name is an XSS probe, and one
# outside the pool.
GIBBS, XSS, OUTSIDE = 4429795, 666, 123456


def espn_draft(*player_ids, status=None):
    picks = [EspnDraftPick(overall=i + 1, round=1, round_pick=i + 1, team_id=i + 1, espn_player_id=pid)
             for i, pid in enumerate(player_ids)]
    return EspnDraft(
        league_id="777", season=2026, name="Office League",
        status=status or ("in_progress" if picks else "pre_draft"), draft_type="SNAKE",
        team_count=TEAMS, rounds=2,
        teams=[EspnDraftTeam(id=i, name=f"Team {i}", abbrev=f"T{i}", slot=i) for i in range(1, TEAMS + 1)],
        picks=picks,
    )


class ScriptedEspn:
    """What ESPN would return right now; tests change it to make picks."""

    def __init__(self):
        self.current = espn_draft()
        self.calls = []
        self._lock = threading.Lock()

    def set(self, value):
        with self._lock:
            self.current = value

    def draft(self, season, league_id, credentials=None):
        with self._lock:
            self.calls.append((league_id, credentials))
            if isinstance(self.current, Exception):
                raise self.current
            return self.current


@pytest.fixture
def espn():
    scripted = ScriptedEspn()
    drafts = LiveDrafts(scripted, poll_seconds=0.2)
    app.dependency_overrides[get_live_drafts] = lambda: drafts
    scripted.drafts = drafts
    return scripted


def connect(page, league="777"):
    page.goto("/draft/?teams=12&slot=5&rounds=15")
    expect(page.locator(".plan-pick")).to_have_count(15)
    page.fill("#live-league", league)
    page.click("#live-connect")


def test_connecting_fills_the_board_and_plans_around_taken_players(page, espn):
    espn.set(espn_draft(GIBBS, XSS, OUTSIDE))
    connect(page)
    expect(page.locator("#live-connected")).to_be_visible()
    expect(page.locator("#live-form")).to_be_hidden()
    expect(page.locator("#live-name")).to_have_text("Office League")
    expect(page.locator("#live-progress")).to_contain_text("3 of 16 picks in")

    # The league's size and rounds replace the page's settings, and lock.
    expect(page.locator(".team-head")).to_have_count(TEAMS)
    expect(page.locator("#rounds")).to_have_value("2")
    expect(page.locator('#league-size button[data-teams="8"]')).to_be_disabled()

    taken = page.locator(".pick.is-taken")
    expect(taken).to_have_count(3)
    expect(taken.nth(0)).to_contain_text("J. Gibbs")
    expect(page.locator(".pick.is-taken", has_text="ESPN player 123456")).to_have_count(1)
    expect(page.locator(".live-pick").first).to_contain_text("ESPN player 123456")

    # Your team defaults to the slot you had (5th); both of its picks are still to come.
    expect(page.locator("#live-team")).to_have_value("5")
    expect(page.locator("#draft-slot")).to_be_disabled()
    expect(page.locator(".plan-pick")).to_have_count(2)
    expect(page.locator(".plan-pick .target-name", has_text="Jahmyr Gibbs")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None
    assert page.problems.errors == []


def test_new_picks_arrive_without_reloading(page, espn):
    espn.set(espn_draft(GIBBS))
    connect(page)
    expect(page.locator(".pick.is-taken")).to_have_count(1)
    espn.set(espn_draft(GIBBS, OUTSIDE, XSS, OUTSIDE + 1, OUTSIDE + 2))
    expect(page.locator(".pick.is-taken")).to_have_count(5)
    expect(page.locator("#live-progress")).to_contain_text("5 of 16 picks in")
    # Pick 5 was your slot's first pick, so only your round 2 pick is left.
    expect(page.locator(".plan-pick")).to_have_count(1)
    expect(page.locator(".plan-pick-head strong")).to_have_text("2.04")
    assert page.problems.errors == []


def test_choosing_your_team_moves_your_slot(page, espn):
    connect(page)
    expect(page.locator("#live-connected")).to_be_visible()
    page.select_option("#live-team", "2")
    expect(page.locator("#draft-slot")).to_have_value("2")
    expect(page.locator(".team-head.is-mine")).to_have_attribute("data-slot", "2")
    expect(page).to_have_url(re.compile(r"slot=2"))


def test_complete_draft(page, espn):
    espn.set(espn_draft(*range(1000, 1000 + 2 * TEAMS), status="complete"))
    connect(page)
    expect(page.locator("#live-progress")).to_contain_text("Draft complete")
    expect(page.locator(".plan-list")).to_contain_text("You've made all your picks")


def test_private_league_without_cookies_explains_what_to_do(page, espn):
    espn.set(LeaguePrivate("777"))
    connect(page)
    expect(page.locator("#live-message")).to_contain_text("private")
    expect(page.locator("#live-form")).to_be_visible()


def test_private_league_sends_both_cookies_and_clears_them(page, espn):
    page.goto("/draft/")
    page.fill("#live-league", "777")
    page.click(".live-private summary")
    page.fill("#live-s2", S2)
    page.click("#live-connect")
    expect(page.locator("#live-message")).to_contain_text("both espn_s2 and SWID")
    assert espn.calls == []

    page.fill("#live-swid", SWID)
    page.click("#live-connect")
    expect(page.locator("#live-connected")).to_be_visible()
    assert espn.calls[0][1].espn_s2 == S2
    assert page.input_value("#live-s2") == "" and page.input_value("#live-swid") == ""
    stored = page.evaluate("JSON.stringify(sessionStorage)")
    assert S2 not in stored and "ABCDEF12" not in stored


def test_bad_league_id_is_caught_before_sending(page, espn):
    page.goto("/draft/")
    page.fill("#live-league", "my league")
    page.click("#live-connect")
    expect(page.locator("#live-message")).to_contain_text("Enter your league ID")
    assert espn.calls == []
    # The error is tied to the field, so a screen reader reads it with the field.
    league = page.locator("#live-league")
    expect(league).to_be_focused()
    expect(league).to_have_attribute("aria-invalid", "true")
    expect(league).to_have_attribute("aria-describedby", "live-message")


def test_half_filled_private_league_marks_the_missing_cookie(page, espn):
    page.goto("/draft/")
    page.fill("#live-league", "777")
    page.locator(".live-connect details summary").click()
    page.fill("#live-s2", "abc")
    page.click("#live-connect")
    swid = page.locator("#live-swid")
    expect(swid).to_be_focused()
    expect(swid).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#live-league")).not_to_have_attribute("aria-invalid", "true")
    assert espn.calls == []


def test_disconnect_forgets_the_session(page, espn):
    connect(page)
    expect(page.locator("#live-connected")).to_be_visible()
    page.click("#live-disconnect")
    expect(page.locator("#live-form")).to_be_visible()
    expect(page.locator("#live-message")).to_contain_text("Disconnected")
    expect(page.locator('#league-size button[data-teams="8"]')).to_be_enabled()
    expect(page.locator(".pick.is-taken")).to_have_count(0)
    page.wait_for_function("() => !sessionStorage.getItem('fcc-live-draft')")
    page.wait_for_timeout(300)
    assert len(espn.drafts) == 0


def test_reloading_picks_the_draft_back_up(page, espn):
    espn.set(espn_draft(GIBBS))
    connect(page)
    expect(page.locator(".pick.is-taken")).to_have_count(1)
    page.reload()
    expect(page.locator("#live-connected")).to_be_visible()
    expect(page.locator(".pick.is-taken")).to_have_count(1)


def test_an_ended_session_shows_the_form_again(page, espn):
    connect(page)
    expect(page.locator("#live-connected")).to_be_visible()
    for session_id in list(espn.drafts._sessions):
        espn.drafts.remove(session_id)
    expect(page.locator("#live-form")).to_be_visible()
    expect(page.locator("#live-message")).to_contain_text(re.compile(r"session (has )?ended"))
    expect(page.locator('#league-size button[data-teams="8"]')).to_be_enabled()


def test_a_stale_saved_session_is_dropped_on_load(page, espn):
    page.goto("/draft/")
    page.evaluate("sessionStorage.setItem('fcc-live-draft', JSON.stringify({sessionId: 'a'.repeat(32), teamId: 1}))")
    page.reload()
    expect(page.locator("#live-message")).to_contain_text("session ended")
    expect(page.locator("#live-form")).to_be_visible()
    assert page.evaluate("sessionStorage.getItem('fcc-live-draft')") is None
