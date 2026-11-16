from datetime import date

from app.services.matchups import Game, MatchupData
from app.services.season import current_week, parse_date, week_on, week_start


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
