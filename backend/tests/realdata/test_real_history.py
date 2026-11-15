"""The defense data and breakdown agree with the real NFL record.

Every number asserted here is from the official league record (Pro Football Reference / NFL.com
season leaders), not from this app, so a parsing, scoring, or team-mapping mistake shows up as
a mismatch with history.
"""

import json

import pytest

from app.defense.scoring import dst_points, points_allowed_score
from app.services.defense_breakdown import rank
from tests.realdata.conftest import FIRST_SEASON

pytestmark = pytest.mark.realdata


def season_total(league, key: str, team: str) -> float:
    return league.unit_stats[key][team] * league.teams[team].games


def leader(league, key: str) -> str:
    values = league.unit_stats[key]
    return max(values, key=values.get) if not key.startswith("points") else min(values, key=values.get)


# (season, team, points allowed): the fewest points allowed in the league that season.
FEWEST_POINTS_ALLOWED = [
    (2018, "CHI", 283), (2019, "NE", 225), (2020, "LAR", 296), (2021, "BUF", 289),
    (2022, "SF", 277), (2023, "BAL", 280), (2024, "LAC", 301),
]


@pytest.mark.parametrize("season,team,points", FEWEST_POINTS_ALLOWED)
def test_fewest_points_allowed(league, season, team, points):
    data = league(season)
    assert round(season_total(data, "points_allowed_pg", team)) == points
    assert leader(data, "points_allowed_pg") == team
    assert data.summary(team).points_allowed_rank == 1


# (season, team, sacks): the league's sack leader and its official total, which counts team
# sacks that no player was credited with.
SACK_LEADERS = [
    (2019, "PIT", 54), (2020, "PIT", 56), (2021, "PIT", 55), (2022, "PHI", 70),
    (2023, "BAL", 60), (2024, "DEN", 63),
]


@pytest.mark.parametrize("season,team,sacks", SACK_LEADERS)
def test_sack_leaders_with_official_totals(league, season, team, sacks):
    data = league(season)
    assert round(season_total(data, "sacks_pg", team)) == sacks
    assert leader(data, "sacks_pg") == team


def test_2018_chiefs_and_steelers_tied_for_the_sack_lead(league):
    data = league(2018)
    assert round(season_total(data, "sacks_pg", "KC")) == round(season_total(data, "sacks_pg", "PIT")) == 52
    assert {t for t, v in data.unit_stats["sacks_pg"].items() if v == max(data.unit_stats["sacks_pg"].values())} \
        == {"KC", "PIT"}


# (season, team, takeaways): interceptions + fumble recoveries, league leader.
TAKEAWAY_LEADERS = [(2018, "CHI", 36), (2019, "PIT", 38), (2021, "DAL", 34)]


@pytest.mark.parametrize("season,team,takeaways", TAKEAWAY_LEADERS)
def test_takeaway_leaders(league, season, team, takeaways):
    data = league(season)
    assert round(season_total(data, "takeaways_pg", team)) == takeaways
    assert leader(data, "takeaways_pg") == team


# (season, team, group, player, stat, total): standout individual seasons. Each player should
# lead his team's position group, with his real total.
STANDOUT_SEASONS = [
    (2018, "LAR", "DL", "Aaron Donald", "sacks", 20.5),  # DPOY, league-leading 20.5 sacks
    (2021, "PIT", "LB", "T.J. Watt", "sacks", 22.5),  # DPOY, tied the single-season record
    (2021, "DAL", "CB", "Trevon Diggs", "ints", 11),  # most interceptions since 1981
    (2022, "SF", "DL", "Nick Bosa", "sacks", 18.5),  # DPOY, league leader
    (2022, "NYJ", "CB", "Sauce Gardner", "pass_defended", 20),  # DROY, league leader
    (2023, "PIT", "LB", "T.J. Watt", "sacks", 19),  # league leader
    (2023, "CLE", "DL", "Myles Garrett", "sacks", 14),  # DPOY
]


@pytest.mark.parametrize("season,team,group,player,stat,total", STANDOUT_SEASONS)
def test_standout_individual_seasons(league, season, team, group, player, stat, total):
    groups = {g.key: g for g in league(season).breakdown(team).groups}
    top = groups[group].players[0]
    assert top.name == player
    assert top.stats[stat] == total


def test_watt_and_diggs_lift_their_groups_to_first(league):
    data = league(2021)
    assert {g.key: g.rank for g in data.breakdown("PIT").groups}["LB"] == 1
    assert {g.key: g.rank for g in data.breakdown("DAL").groups}["CB"] == 1


@pytest.mark.parametrize("season", range(FIRST_SEASON, 2026))
def test_every_team_and_game_is_there(league, season):
    """32 teams every season (the Raiders included while they were in Oakland), 16 games each
    through 2020 and 17 from 2021, except Buffalo and Cincinnati in 2022, whose Week 17 game
    was cancelled after Damar Hamlin's cardiac arrest."""
    data = league(season)
    assert len(data.teams) == 32 and "LV" in data.teams
    games = {team: t.games for team, t in data.teams.items()}
    expected = 16 if season <= 2020 else 17
    short = {"BUF", "CIN"} if season == 2022 else set()
    assert {team for team, g in games.items() if g != expected} == short
    assert all(games[team] == 16 for team in short)


@pytest.mark.parametrize("season", range(FIRST_SEASON, 2026))
def test_ranks_are_consistent(league, season):
    data = league(season)
    summary = data.league()
    for key in ("DL", "LB", "CB", "S"):
        ranks = sorted(t.group_ranks[key] for t in summary.teams)
        assert ranks[0] == 1 and ranks[-1] <= 32
    # Ranks follow the values: a better rank never has a worse number.
    for key, lower in (("points_allowed_pg", True), ("dst_ppg", False), ("sack_rate", False)):
        values = data.unit_stats[key]
        ordered = sorted(values, key=lambda t: values[t], reverse=not lower)
        assert [rank(values, t, lower) for t in ordered] == sorted(rank(values, t, lower) for t in ordered)


@pytest.mark.parametrize("season", range(FIRST_SEASON, 2026))
def test_player_sacks_add_up_to_the_team_and_play_by_play_adds_team_sacks(real_db, league, season):
    """The team stat line is the sum of every player's credited sacks; the position groups hold
    all of them except the odd sack by a player listed on offense (Patrick Ricard, Baltimore's
    two-way fullback and defensive lineman, had one in 2019). Play-by-play also counts team
    sacks, so it's never lower and only a few higher."""
    data = league(season)

    def by_team(sql):
        totals = {}
        for team, stats in real_db.execute(sql, (season,)):
            totals[team] = totals.get(team, 0.0) + float(json.loads(stats).get("def_sacks") or 0)
        return totals

    credited = by_team("SELECT team, stats FROM team_week_stats WHERE season = ? AND season_type = 'REG'")
    every_player = by_team("SELECT team, stats FROM player_week_stats WHERE season = ? AND season_type = 'REG'")
    for team in data.teams:
        groups = sum(p["stats"]["sacks"] for (t, _), group in data.players.items() if t == team
                     for p in group.values())
        # nflverse's player files are missing one credited sack in three team-games (2019 SEA
        # week 1, 2019 NE week 5, 2020 NYG week 10); everywhere else they match exactly.
        assert 0 <= credited[team] - every_player.get(team, 0.0) <= 1
        assert 0 <= credited[team] - groups <= 2
        assert 0 <= season_total(data, "sacks_pg", team) - credited[team] <= 4


def test_patrick_ricards_2019_sack_is_outside_the_defensive_groups(league):
    data = league(2019)
    names = {p["name"] for (t, _), group in data.players.items() if t == "BAL" for p in group.values()}
    assert "Patrick Ricard" not in names  # listed as a running back (fullback)


def test_dolphins_70_broncos_20(dst_builder):
    """Week 3 of 2023: Miami 70, Denver 20, the most points in a game since 1966. Denver's
    defense gets the worst points-allowed tier."""
    rows = {(r.season, r.week, r.defense): r for r in dst_builder.rows([2023])}
    den, mia = rows[(2023, 3, "DEN")], rows[(2023, 3, "MIA")]
    assert den.opponent == "MIA" and mia.opponent == "DEN" and mia.home
    assert points_allowed_score(70) == -4
    stats = dst_builder.defense[(2023, "DEN")][3]
    assert den.target == dst_points(stats, 70, dst_builder._sacks_made(2023, "DEN", 3))
    assert mia.target > den.target
    # Vegas had Miami favored by 6 with a 47.5 total, so the model saw Denver's defense facing
    # the higher implied score.
    assert den.features["opp_implied_points"] > mia.features["opp_implied_points"]
    assert den.features["def_favored_by"] == -6.0
