"""The season calendar: which week it is, and rest-of-season projections as weeks pass.

Start/sit, tiers, tags, defenses, and player values all use `current_week`, so they agree on
the week. `rest_of_season` recomputes a player's rest-of-season projection from their weekly
projections, so weeks that have been played stop counting without a fresh sync.
"""

from collections.abc import Container
from datetime import date, timedelta

from app.config import REGULAR_SEASON_END_WEEK
from app.models.player import Player
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


def played_teams(matchups: MatchupData, week: int, day: date | None = None) -> frozenset[str]:
    """Teams whose game in `week` is over (final, or its date has passed)."""
    iso = (day or today()).isoformat()
    return frozenset(
        team for team, games in matchups.schedule.items()
        if (g := games.get(week)) is not None and (g.played or (g.gameday is not None and g.gameday < iso))
    )


def rest_of_season(player: Player, week: int, played: Container[str] = frozenset()) -> Player:
    """The player with `ros_projection` and `projection_sources` summed over their weekly
    projections from `week` on (skipping `week` itself if their team has played it).

    Players without weekly projections (sample data, old caches) are returned unchanged."""
    if not player.weekly_projections:
        return player
    skip_current = player.team in played
    totals: dict[str, float] = {}
    for w, sources in player.weekly_projections.items():
        if w < week or (w == week and skip_current):
            continue
        for source, points in sources.items():
            totals[source] = totals.get(source, 0.0) + points
    sources = {k: round(v, 2) for k, v in totals.items() if v}
    ros = round(sum(sources.values()) / len(sources), 2) if sources else 0.0
    if ros == player.ros_projection and sources == player.projection_sources:
        return player
    return player.model_copy(update={"ros_projection": ros, "projection_sources": sources})
