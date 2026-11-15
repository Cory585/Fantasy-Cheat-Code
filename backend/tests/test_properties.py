"""Property-based tests: invariants that must hold for any input, checked with Hypothesis."""

import json

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.api.ratelimit import RateLimiter, Rule
from app.api.static import wants_html
from app.data.sources.espn_athletes import (
    MAX_CATEGORIES,
    MAX_COLUMNS,
    MAX_SEASONS,
    MAX_TEXT,
    AthleteNotFound,
    parse_bio,
    parse_stats,
)
from app.models.player import Player, Position
from app.services.draft import DraftPlanner, snake_picks
from app.services.player_profile import format_height, percentile

# ---------- Snake draft order ----------


@given(teams=st.integers(1, 20), rounds=st.integers(1, 25))
def test_every_pick_belongs_to_exactly_one_slot(teams, rounds):
    owners = {}
    for slot in range(1, teams + 1):
        for pick in snake_picks(teams, slot, rounds):
            assert pick.overall not in owners
            owners[pick.overall] = slot
    assert sorted(owners) == list(range(1, teams * rounds + 1))


@given(teams=st.integers(1, 20), rounds=st.integers(1, 25), data=st.data())
def test_picks_are_ordered_and_labeled_consistently(teams, rounds, data):
    slot = data.draw(st.integers(1, teams))
    picks = snake_picks(teams, slot, rounds)
    assert [p.round for p in picks] == list(range(1, rounds + 1))
    for p in picks:
        assert 1 <= p.pick_in_round <= teams
        assert p.overall == (p.round - 1) * teams + p.pick_in_round
        assert p.label == f"{p.round}.{p.pick_in_round:02d}"
    gaps = [b.overall - a.overall - 1 for a, b in zip(picks, picks[1:])]
    # Gaps alternate between 2*(teams - slot) and 2*(slot - 1).
    for i, gap in enumerate(gaps):
        assert gap == (2 * (teams - slot) if i % 2 == 0 else 2 * (slot - 1))


# ---------- Draft planner ----------

positions = st.sampled_from(list(Position))
player_pools = st.lists(
    st.tuples(positions, st.floats(0, 400, allow_nan=False), st.one_of(st.none(), st.floats(1, 300, allow_nan=False))),
    min_size=0,
    max_size=80,
)


def _build(pool):
    players, adp = [], {}
    for i, (position, projection, player_adp) in enumerate(pool):
        players.append(Player(id=str(i), name=f"P{i}", position=position, team="X",
                              ros_projection=projection, espn_id=i + 1))
        if player_adp is not None:
            adp[i + 1] = player_adp
    return players, adp


@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(pool=player_pools, teams=st.integers(4, 16), rounds=st.integers(1, 18), data=st.data())
def test_plan_invariants(pool, teams, rounds, data):
    slot = data.draw(st.integers(1, teams))
    players, adp = _build(pool)
    plan = DraftPlanner(players, adp).plan(teams=teams, slot=slot, rounds=rounds, targets_per_pick=5)

    # The board is a ranking: every player at most once, ranks contiguous from 1.
    assert [t.rank for t in plan.board] == list(range(1, len(plan.board) + 1))
    assert len({t.player_id for t in plan.board}) == len(plan.board)
    assert len(plan.board) == min(len(players), teams * rounds)

    for i, planned in enumerate(plan.picks):
        overall = planned.pick.overall
        assert all(t.rank >= overall for t in planned.targets)
        assert [t.rank for t in planned.targets] == list(range(overall, overall + len(planned.targets)))
        is_last = i == len(plan.picks) - 1
        if is_last:
            assert planned.picks_until_next is None and planned.position_mix == {}
        else:
            assert planned.picks_until_next == plan.picks[i + 1].pick.overall - overall - 1
            assert sum(planned.position_mix.values()) <= planned.picks_until_next
            assert all(n > 0 for n in planned.position_mix.values())


@given(pool=player_pools)
def test_adp_order_is_respected_among_players_with_usable_adp(pool):
    players, adp = _build(pool)
    plan = DraftPlanner(players, adp).plan(teams=1, slot=1, rounds=1, targets_per_pick=len(players) or 1)
    shown = [t.adp for t in plan.picks[0].targets if t.adp is not None]
    assert shown == sorted(shown)


# ---------- Rate limiter ----------


@given(
    burst=st.integers(1, 20),
    rate=st.floats(0.1, 10, allow_nan=False),
    steps=st.lists(st.floats(0, 3, allow_nan=False), min_size=1, max_size=200),
)
def test_rate_limiter_never_allows_more_than_burst_plus_refill(burst, rate, steps):
    now = [0.0]
    limiter = RateLimiter([Rule("r", burst, rate, lambda p: True)], clock=lambda: now[0])
    allowed = 0
    for step in steps:
        now[0] += step
        wait = limiter.check("client", "/")
        if wait is None:
            allowed += 1
        else:
            assert wait > 0
    assert allowed <= burst + rate * now[0] + 1e-6


# ---------- Profile helpers ----------


@given(value=st.floats(0, 100, allow_nan=False), others=st.lists(st.floats(0, 100, allow_nan=False), max_size=50),
       lower=st.booleans())
def test_percentile_is_bounded(value, others, lower):
    result = percentile(value, [value, *others], lower)
    if not others:
        assert result is None
    else:
        assert 0 <= result <= 100


@given(values=st.lists(st.floats(4.2, 5.5, allow_nan=False), min_size=3, max_size=40, unique=True))
def test_faster_forty_never_ranks_lower(values):
    ordered = sorted(values)
    ranks = [percentile(v, values, lower_is_better=True) for v in ordered]
    assert ranks == sorted(ranks, reverse=True)


@given(st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True), st.integers(-10, 120)))
def test_format_height_never_crashes(inches):
    try:
        result = format_height(inches)
    except (ValueError, OverflowError):
        pytest.fail("format_height raised")
    assert result is None or "'" in result


# ---------- Untrusted ESPN JSON ----------

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=300),
    lambda children: st.lists(children, max_size=6) | st.dictionaries(st.text(max_size=20), children, max_size=6),
    max_leaves=40,
)

athlete_like = st.fixed_dictionaries({}, optional={
    "displayName": json_values, "age": json_values, "position": json_values, "team": json_values,
    "college": json_values, "headshot": json_values, "displayHeight": json_values, "status": json_values,
})


@settings(max_examples=300)
@given(data=st.one_of(json_values, st.fixed_dictionaries({"athlete": athlete_like})))
def test_parse_bio_handles_any_json(data):
    assume(isinstance(data, dict))
    try:
        bio = parse_bio(data, 42)
    except AthleteNotFound:
        return
    json.dumps(bio)  # serializable
    for key, value in bio.items():
        assert value is None or isinstance(value, (str, int))
        if isinstance(value, str) and key != "headshot_url":
            assert len(value) <= MAX_TEXT and "\n" not in value
    assert bio["headshot_url"].startswith("https://a.espncdn.com/")


stats_like = st.fixed_dictionaries({}, optional={
    "teams": json_values,
    "categories": st.lists(st.fixed_dictionaries({}, optional={
        "name": json_values, "displayName": json_values,
        "labels": st.lists(json_values, max_size=30), "displayNames": json_values,
        "statistics": st.lists(json_values, max_size=35), "totals": json_values,
    }), max_size=15),
})


@settings(max_examples=300)
@given(data=st.one_of(stats_like, st.dictionaries(st.text(max_size=10), json_values, max_size=4)))
def test_parse_stats_handles_any_json(data):
    categories = parse_stats(data)
    json.dumps(categories)
    assert len(categories) <= MAX_CATEGORIES
    for category in categories:
        n = len(category["columns"])
        assert 0 < n <= MAX_COLUMNS
        assert len(category["seasons"]) <= MAX_SEASONS
        for season in category["seasons"]:
            assert isinstance(season["year"], int)
            assert len(season["values"]) == n
        assert category["totals"] == [] or len(category["totals"]) == n


# ---------- Accept header parsing ----------


@given(st.binary(max_size=200))
def test_wants_html_never_crashes(raw):
    wants_html({"headers": [(b"accept", raw)]})


@given(q=st.floats(0, 1, allow_nan=False))
def test_wants_html_respects_quality(q):
    header = f"text/html;q={q}".encode()
    assert wants_html({"headers": [(b"accept", header)]}) == (q > 0)


# ---------- Start/sit lineups ----------

import itertools  # noqa: E402

from app.models.start_sit import SLOT_POSITIONS, LineupSlot  # noqa: E402
from app.services.matchups import MatchupData  # noqa: E402
from app.services.start_sit import StartSitAdvisor  # noqa: E402

roster_players = st.lists(
    st.tuples(st.sampled_from([Position.QB, Position.RB, Position.WR, Position.TE]), st.floats(0, 30)),
    min_size=1, max_size=7,
)
lineup_slots = st.fixed_dictionaries({
    LineupSlot.QB: st.integers(0, 1), LineupSlot.RB: st.integers(0, 2), LineupSlot.WR: st.integers(0, 2),
    LineupSlot.TE: st.integers(0, 1), LineupSlot.FLEX: st.integers(0, 1), LineupSlot.SUPERFLEX: st.integers(0, 1),
})


@settings(max_examples=80, deadline=None)
@given(players=roster_players, slots=lineup_slots)
def test_lineup_is_as_good_as_any_assignment(players, slots):
    assume(0 < sum(slots.values()) <= 5)  # keeps the brute force small
    roster = [
        Player(id=f"p{i}", name=f"P{i}", position=pos, team="AAA", weekly_projections={1: {"x": round(pts, 2)}})
        for i, (pos, pts) in enumerate(players)
    ]
    advisor = StartSitAdvisor(MatchupData(), None, (1, 17), simulations=50)
    result = advisor.lineup(roster, slots, week=1)

    starters = [s.starter.player.id for s in result.lineup if s.starter]
    assert len(starters) == len(set(starters))
    assert {pw.player.id for pw in result.bench} | set(starters) == {p.id for p in roster}
    for spot in result.lineup:
        if spot.starter:
            assert spot.starter.player.position in SLOT_POSITIONS[spot.slot]

    # Brute force: every way to put distinct players (or nobody) in the spots.
    spots = [slot for slot, n in slots.items() for _ in range(n)]
    points = {p.id: p.week_projection(1) for p in roster}
    options = [None] + roster
    best = 0.0
    for choice in itertools.product(options, repeat=len(spots)):
        chosen = [p for p in choice if p is not None]
        if len({p.id for p in chosen}) != len(chosen):
            continue
        if all(p is None or p.position in SLOT_POSITIONS[s] for p, s in zip(choice, spots)):
            best = max(best, sum(points[p.id] for p in chosen))
    assert result.projected_total == pytest.approx(best, abs=0.1)


# ---------- Tiers ----------

from app.services.tiers import tier_breaks  # noqa: E402

tier_values = st.lists(st.floats(-500, 500, allow_nan=False), max_size=120).map(lambda v: sorted(v, reverse=True))


@given(values=tier_values)
def test_tiers_cover_every_value_once_in_order(values):
    breaks = tier_breaks(values)
    if not values:
        assert breaks == []
        return
    assert breaks[0] == 0
    assert breaks == sorted(set(breaks))
    assert all(0 <= b < len(values) for b in breaks)


@given(values=tier_values)
def test_tiers_never_overlap(values):
    breaks = tier_breaks(values)
    ends = breaks[1:] + [len(values)]
    tiers = [values[a:b] for a, b in zip(breaks, ends)]
    assert all(tiers)
    for upper, lower in zip(tiers, tiers[1:]):
        assert min(upper) >= max(lower)


@given(values=tier_values, scale=st.sampled_from([0.125, 0.5, 2.0, 16.0]))
def test_tiers_ignore_units(values, scale):
    # Powers of two scale exactly, so points vs. hundreds of points can't change the tiers.
    assert tier_breaks(values) == tier_breaks([v * scale for v in values])
