import httpx
import pytest

from app.data.sources.espn_draft import (
    EspnCredentials,
    EspnDraftSource,
    LeagueNotFound,
    LeaguePrivate,
    parse_draft,
)
from app.data.sources.http import RateLimitedClient

SWID = "{ABCDEF12-3456-7890-ABCD-EF1234567890}"


def league_json(picks=(), drafted=False, in_progress=False):
    return {
        "id": 777,
        "settings": {
            "name": "Office League",
            "size": 4,
            "draftSettings": {"type": "SNAKE", "pickOrder": [3, 1, 4, 2]},
            # 9 starters + 6 bench + 1 IR (not drafted) = 15 rounds.
            "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1,
                                                    "20": 6, "21": 1, "5": 0}},
        },
        "teams": [
            {"id": 1, "abbrev": "ONE", "name": "Team One"},
            {"id": 2, "abbrev": "TWO", "location": "Second", "nickname": "Place"},
            {"id": 3, "abbrev": "THR", "name": "Three Peat"},
            {"id": 4, "abbrev": "FOR"},
        ],
        "draftDetail": {"drafted": drafted, "inProgress": in_progress, "picks": list(picks)},
    }


def pick(overall, team, player, round_=1, keeper=False):
    return {"overallPickNumber": overall, "roundId": round_, "roundPickNumber": overall, "teamId": team,
            "playerId": player, "keeper": keeper}


def test_parses_teams_in_draft_order():
    draft = parse_draft(league_json(), "777", 2026)
    assert draft.status == "pre_draft"
    assert (draft.name, draft.team_count, draft.rounds, draft.draft_type) == ("Office League", 4, 15, "SNAKE")
    assert [(t.id, t.slot) for t in draft.teams] == [(3, 1), (1, 2), (4, 3), (2, 4)]
    assert [t.name for t in draft.teams] == ["Three Peat", "Team One", "Team 4", "Second Place"]


def test_parses_picks_in_order_and_skips_empty_ones():
    data = league_json([pick(2, 1, 200), pick(1, 3, 100, keeper=True), pick(3, 4, -1)], in_progress=True)
    draft = parse_draft(data, "777", 2026)
    assert draft.status == "in_progress"
    assert [(p.overall, p.team_id, p.espn_player_id) for p in draft.picks] == [(1, 3, 100), (2, 1, 200)]
    assert draft.picks[0].keeper


def test_picks_mean_in_progress_even_without_the_flag():
    assert parse_draft(league_json([pick(1, 3, 100)]), "777", 2026).status == "in_progress"


def test_complete_draft():
    assert parse_draft(league_json([pick(1, 3, 100)], drafted=True), "777", 2026).status == "complete"


def test_rounds_cover_the_latest_pick_and_fall_back_to_15():
    data = league_json([pick(1, 3, 100, round_=17)])
    assert parse_draft(data, "777", 2026).rounds == 17
    data = {"settings": {}, "draftDetail": {}}
    draft = parse_draft(data, "777", 2026)
    assert (draft.rounds, draft.team_count, draft.teams, draft.picks) == (15, 0, [], [])


def test_rejects_a_non_object():
    with pytest.raises(ValueError):
        parse_draft([], "777", 2026)


def test_cookie_header_normalizes_the_swid_braces():
    creds = EspnCredentials(espn_s2="AEB%2Babc", swid="abcdef12-3456-7890-abcd-ef1234567890")
    assert creds.cookie_header() == f"espn_s2=AEB%2Babc; SWID={SWID}"


def source(handler):
    return EspnDraftSource(RateLimitedClient(min_interval=0, max_retries=0, transport=httpx.MockTransport(handler)))


def test_fetch_requests_the_draft_views_with_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=league_json())

    draft = source(handler).draft(2026, "777", EspnCredentials(espn_s2="s" * 30, swid=SWID))
    assert draft.league_id == "777"
    request = seen[0]
    assert request.url.path.endswith("/seasons/2026/segments/0/leagues/777")
    assert request.url.params.get_list("view") == ["mDraftDetail", "mSettings", "mTeams"]
    assert request.headers["Cookie"] == f"espn_s2={'s' * 30}; SWID={SWID}"


def test_fetch_without_cookies_sends_none():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=league_json())

    source(handler).draft(2026, "777")
    assert "Cookie" not in seen[0].headers


@pytest.mark.parametrize(("status", "error"), [(401, LeaguePrivate), (403, LeaguePrivate), (404, LeagueNotFound),
                                               (400, LeagueNotFound)])
def test_fetch_maps_espn_errors(status, error):
    with pytest.raises(error):
        source(lambda request: httpx.Response(status)).draft(2026, "777")


def test_fetch_raises_other_errors():
    with pytest.raises(httpx.HTTPStatusError):
        source(lambda request: httpx.Response(500)).draft(2026, "777")


def test_fetch_rejects_a_bad_league_id():
    with pytest.raises(ValueError):
        source(lambda request: httpx.Response(200)).draft(2026, "../777")


@pytest.mark.parametrize("body", [
    {"draftDetail": "drafting"},
    {"draftDetail": {"picks": ["not a pick"]}},
    {"settings": ["not", "an", "object"]},
    {"settings": {"draftSettings": 5}},
    {"settings": {"rosterSettings": {"lineupSlotCounts": [1, 2]}}},
    {"draftDetail": {"picks": [{"playerId": 5, "overallPickNumber": "one"}]}},
])
def test_fetch_turns_an_unexpected_response_shape_into_a_value_error(body):
    with pytest.raises(ValueError):
        source(lambda request: httpx.Response(200, json=body)).draft(2026, "777")
