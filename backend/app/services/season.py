"""The season calendar: which week it is.

Start/sit, tiers, tags, and defenses all use `current_week`, so they agree on the week.
"""

from datetime import date, timedelta

from app.config import REGULAR_SEASON_END_WEEK
from app.services.matchups import MatchupData


def today() -> date:
    """Today's date (a seam for tests)."""
    return date.today()


def week_start(season: int, season_start: date | None = None) -> date:
    """The Tuesday that week 1 begins on: the Tuesday on or before the season's first game
    (Sleeper's `season_start_date` when the sync saved it), else the Tuesday after Labor Day
    (the NFL kicks off the Thursday after it). Weeks turn over each Tuesday, after Monday
    night's game."""
    if season_start is None:
        sept_1 = date(season, 9, 1)
        labor_day = sept_1 + timedelta(days=(0 - sept_1.weekday()) % 7)  # first Monday in September
        return labor_day + timedelta(days=1)
    return season_start - timedelta(days=(season_start.weekday() - 1) % 7)


def week_on(day: date, season: int, season_start: date | None = None) -> int:
    """The regular-season week `day` falls in by the calendar (1 before the season starts,
    the last week once it's over)."""
    days = (day - week_start(season, season_start)).days
    return min(max(days // 7 + 1, 1), REGULAR_SEASON_END_WEEK)


def parse_date(value) -> date | None:
    """An ISO date (or datetime) string from cache metadata, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def current_week(
    matchups: MatchupData,
    weeks: tuple[int, int],
    season: int | None = None,
    season_start: date | None = None,
    day: date | None = None,
) -> int:
    """The week to show by default, within the synced `weeks` (first, last).

    With a collected schedule: the first week with a game still to be played. A game with no
    score whose date has passed was almost certainly played after the data was collected, so
    it doesn't hold the week back. Without one, the calendar says (see `week_on`). With neither
    a schedule nor a season (sample data), the first synced week."""
    first, last = weeks
    day = day or today()
    if matchups.available:
        iso = day.isoformat()
        for week in range(first, last + 1):
            games = [w[week] for w in matchups.schedule.values() if week in w]
            if any(not g.played and (g.gameday is None or g.gameday >= iso) for g in games):
                return week
        return last
    if season is None:
        return first
    return min(max(week_on(day, season, season_start), first), last)

