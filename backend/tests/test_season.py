from datetime import date, datetime, timezone

from app.models.player import Player, Position
from app.services.matchups import Game, MatchupData
from app.services.season import current_week, local_date, parse_date, played_teams, rest_of_season, season_over, week_on, week_start


def test_week_one_starts_the_tuesday_after_labor_day():
    assert week_start(2026) == date(2026, 9, 8)  # Labor Day is Sept 7; kickoff Thursday Sept 10
    assert week_start(2025) == date(2025, 9, 2)


def test_week_one_starts_the_tuesday_before_sleepers_season_start():
    assert week_start(2026, date(2026, 9, 10)) == date(2026, 9, 8)
    assert week_start(2026, date(2026, 9, 8)) == date(2026, 9, 8)


def test_weeks_turn_over_on_tuesdays():
    assert week_on(date(2026, 8, 1), 2026) == 1  # preseason
    assert week_on(date(2026, 9, 14), 2026) == 1  # Monday night of week 1
    assert week_on(date(2026, 9, 15), 2026) == 2
    assert week_on(date(2026, 11, 16), 2026) == 10
    assert week_on(date(2026, 11, 18), 2026) == 11
    assert week_on(date(2027, 2, 1), 2026) == 18  # season over: the last week


def test_current_week_without_a_schedule_uses_the_calendar_within_the_synced_weeks():
    empty = MatchupData()
    nov_18 = date(2026, 11, 18)
    assert current_week(empty, (9, 18), 2026, day=nov_18) == 11
    assert current_week(empty, (12, 18), 2026, day=nov_18) == 12  # not before the first synced week
    assert current_week(empty, (9, 10), 2026, day=nov_18) == 10  # nor after the last
    assert current_week(empty, (9, 18), None, day=nov_18) == 9  # no season (sample data): first week


def test_current_week_prefers_the_schedule():
    data = MatchupData(schedule={"AAA": {9: Game(9, "BBB", True, True), 10: Game(10, "CCC", True, False)}})
    assert current_week(data, (9, 18), 2026, day=date(2026, 9, 20)) == 10


def test_parse_date():
    assert parse_date("2026-09-10") == date(2026, 9, 10)
    assert parse_date("2026-09-10T00:00:00Z") == date(2026, 9, 10)
    assert parse_date("soon") is None and parse_date(None) is None


def weekly_player(**kw):
    weekly = {10: {"sleeper": 10.0, "espn": 12.0}, 11: {"sleeper": 20.0}, 12: {"sleeper": 30.0, "espn": 28.0}}
    return Player(id="p", name="P", position=Position.WR, team="AAA", ros_projection=55.0,
                  projection_sources={"sleeper": 60.0, "espn": 40.0}, weekly_projections=weekly, **kw)


def test_rest_of_season_sums_weekly_projections_from_the_week_on():
    p = weekly_player()
    assert rest_of_season(p, 10) == p.model_copy(update={"ros_projection": 50.0})  # (60 + 40) / 2
    later = rest_of_season(p, 11)
    assert later.projection_sources == {"sleeper": 50.0, "espn": 28.0}
    assert later.ros_projection == 39.0
    assert rest_of_season(p, 13).ros_projection == 0.0 and rest_of_season(p, 13).projection_sources == {}


def test_rest_of_season_skips_the_week_once_the_team_has_played():
    assert rest_of_season(weekly_player(), 11, {"AAA"}).ros_projection == 29.0
    assert rest_of_season(weekly_player(), 11, {"BBB"}).ros_projection == 39.0


def test_rest_of_season_leaves_players_without_weekly_projections_alone():
    p = Player(id="s", name="S", position=Position.QB, team="KC", ros_projection=210.0)
    assert rest_of_season(p, 12) is p


def test_played_teams_counts_final_games_and_past_dates():
    data = MatchupData(schedule={
        "AAA": {11: Game(11, "BBB", True, True)},
        "CCC": {11: Game(11, "DDD", True, False, gameday="2026-11-19")},
        "EEE": {11: Game(11, "FFF", True, False, gameday="2026-11-22")},
    })
    assert played_teams(data, 11, date(2026, 11, 20)) == {"AAA", "CCC"}


def test_local_date_converts_utc_timestamps_to_the_local_day():
    utc = "2026-11-11T03:30:00+00:00"
    assert local_date(utc) == datetime(2026, 11, 11, 3, 30, tzinfo=timezone.utc).astimezone().date()
    assert local_date("2026-11-11T03:30:00Z") == local_date(utc)
    assert local_date("2026-11-11T03:30:00") == date(2026, 11, 11)  # no zone: taken as local
    assert local_date("2026-11-11") == date(2026, 11, 11)
    assert local_date("never") is None and local_date(None) is None


def test_season_is_over_once_the_last_week_is_played():
    data = MatchupData(schedule={
        "AAA": {18: Game(18, "BBB", True, True)},
        "BBB": {18: Game(18, "AAA", False, True)},
        "CCC": {18: Game(18, "DDD", True, False, gameday="2027-01-10")},
        "DDD": {18: Game(18, "CCC", False, False, gameday="2027-01-10")},
    })
    assert not season_over(data, (10, 18), 2026, day=date(2027, 1, 9))
    assert season_over(data, (10, 18), 2026, day=date(2027, 1, 11))


def test_season_is_over_by_the_calendar_without_a_schedule():
    empty = MatchupData()
    assert not season_over(empty, (10, 18), 2026, day=date(2027, 1, 11))  # Monday of week 18
    assert season_over(empty, (10, 18), 2026, day=date(2027, 1, 12))
    assert not season_over(empty, (1, 18), None, day=date(2027, 6, 1))  # sample data
