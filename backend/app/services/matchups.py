"""Week-by-week matchup data for start/sit: each team's opponent and Vegas lines by week,
how much each defense allows to each position, and every player's weekly points this season.

Built from the collected database (`python -m app.data.collect`). Without it, start/sit
falls back to provider projections only.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from app.models.player import Position
from app.models.trade import ScoringFormat
from app.services.outlook import OutlookBuilder


@dataclass(frozen=True)
class Game:
    week: int
    opponent: str
    home: bool
    played: bool
    # The team's implied points from the Vegas spread and total (None without lines).
    implied_total: float | None = None
    # Points the team is favored by (negative = underdog).
    favored_by: float | None = None


@dataclass
class MatchupData:
    season: int | None = None
    # team -> week -> game. Weeks a team has no game (byes) are absent.
    schedule: dict[str, dict[int, Game]] = field(default_factory=dict)
    # position -> opponent -> points allowed vs league average (>1 = easier), as in OutlookBuilder.
    defense_factors: dict[Position, dict[str, float]] = field(default_factory=dict)
    # gsis_id -> week -> fantasy points (regular season, this season).
    history: dict[str, dict[int, float]] = field(default_factory=dict)
    gsis_by_espn: dict[int, str] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.schedule)

    def game(self, team: str, week: int) -> Game | None:
        return self.schedule.get(team, {}).get(week)

    def has_schedule(self, team: str) -> bool:
        return team in self.schedule

    def defense_rank(self, position: Position, opponent: str) -> tuple[int, int] | None:
        """(rank, teams) of a defense for a position; 1 = easiest matchup."""
        factors = self.defense_factors.get(position) or {}
        if opponent not in factors:
            return None
        ordered = sorted(factors, key=lambda team: factors[team], reverse=True)
        return ordered.index(opponent) + 1, len(ordered)

    def implied_rank(self, team: str, week: int) -> tuple[int, int] | None:
        """(rank, teams) of a team's implied points among teams playing that week; 1 = highest."""
        totals = {
            t: g.implied_total for t, weeks in self.schedule.items()
            if (g := weeks.get(week)) is not None and g.implied_total is not None
        }
        if team not in totals:
            return None
        ordered = sorted(totals, key=lambda t: totals[t], reverse=True)
        return ordered.index(team) + 1, len(ordered)

    def player_history(self, espn_id: int | None) -> dict[int, float]:
        gsis = self.gsis_by_espn.get(espn_id) if espn_id else None
        return self.history.get(gsis, {}) if gsis else {}


def load_matchups(conn: sqlite3.Connection, season: int, scoring: ScoringFormat) -> MatchupData:
    builder = OutlookBuilder(conn, season=season, scoring=scoring)
    return MatchupData(
        season=season,
        schedule=_schedule(conn, season),
        defense_factors=builder.defense_factors(),
        history=_history(conn, season, builder),
        gsis_by_espn=builder.gsis_by_espn(),
    )


def _schedule(conn: sqlite3.Connection, season: int) -> dict[str, dict[int, Game]]:
    schedule: dict[str, dict[int, Game]] = defaultdict(dict)
    rows = conn.execute(
        """SELECT week, away_team, home_team, away_score, home_score, spread_line, total_line
           FROM games WHERE season = ? AND game_type = 'REG'""",
        (season,),
    )
    for week, away, home, away_score, home_score, spread, total in rows:
        played = away_score is not None and home_score is not None
        # nflverse spread_line is positive when the home team is favored.
        home_implied = away_implied = None
        if spread is not None and total is not None:
            home_implied = round((total + spread) / 2, 1)
            away_implied = round((total - spread) / 2, 1)
        schedule[home][week] = Game(week, away, True, played, home_implied, spread)
        schedule[away][week] = Game(week, home, False, played, away_implied, -spread if spread is not None else None)
    return dict(schedule)


def _history(conn: sqlite3.Connection, season: int, builder: OutlookBuilder) -> dict[str, dict[int, float]]:
    history: dict[str, dict[int, float]] = defaultdict(dict)
    rows = conn.execute(
        """SELECT gsis_id, week, position, fantasy_points, fantasy_points_ppr, stats
           FROM player_week_stats
           WHERE season = ? AND season_type = 'REG' AND position IN ('QB', 'RB', 'WR', 'TE', 'K')""",
        (season,),
    )
    for gsis, week, position, std, ppr, stats in rows:
        history[gsis][week] = round(builder.points(position, std, ppr, stats), 2)
    return dict(history)
