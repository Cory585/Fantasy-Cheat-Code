"""Player outlook: remaining strength of schedule and recent form.

Built from the collected database (`python -m app.data.collect`).

Strength of schedule (SOS)
    For each defense, average fantasy points allowed per game to each position this
    season, relative to the league average. A player's SOS factor is the mean over their
    remaining opponents: 1.10 means opponents allow 10% more than average. Early-season
    samples are noisy, so each defense's ratio is shrunk toward 1.0
    (weight = games / (games + SOS_SHRINK_GAMES)).
    - Kickers use kicker points allowed (computed from field goals and extra points,
      since nflverse reports 0 fantasy points for kickers).
    - Defenses use the opponent's points scored per game, inverted (weaker offenses are
      easier).

Recent form
    Average fantasy points over a player's last FORM_GAMES games (within the last
    FORM_WINDOW_WEEKS weeks), projected over their remaining games and adjusted by SOS.
    Provider projections already account for opponents, so SOS is applied only to this
    form-based estimate, never on top of the provider projection, to avoid counting the
    schedule twice. The form estimate is capped at +/-FORM_CAP of the provider projection:
    recent form can't see role changes (a backup who started while the starter was hurt),
    which the providers already account for.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from app.config import REGULAR_SEASON_END_WEEK
from app.models.player import Player, PlayerOutlook, Position
from app.models.trade import ScoringFormat

FORM_GAMES = 4  # games used for recent form
MIN_FORM_GAMES = 3  # fewer than this and form isn't used
FORM_WINDOW_WEEKS = 6  # only games this recent count toward form
FORM_CAP = 0.25  # form estimate is limited to provider projection +/- 25%
SOS_SHRINK_GAMES = 3.0
SCHEDULE_TIER_SIZE = 8  # top/bottom 8 of 32 schedules are labelled easy/hard
# Players listed with these statuses are likely to miss games, which recent form can't
# see; their outlook relies on the provider projection instead.
FORM_EXCLUDED_INJURIES = {"O", "IR"}

SKILL_POSITIONS = (Position.QB, Position.RB, Position.WR, Position.TE)


@dataclass
class OutlookBuilder:
    conn: sqlite3.Connection
    season: int
    scoring: ScoringFormat
    end_week: int = REGULAR_SEASON_END_WEEK
    form_weight: float = 0.3
    # Games before this week don't count as remaining, even without a score collected.
    start_week: int = 1
    # Teams whose `start_week` game is over (see app.services.season.played_teams), so it
    # isn't remaining either: the same games rest-of-season projections leave out.
    played: frozenset[str] = frozenset()

    # ---------- public ----------

    def build(self, players: list[Player]) -> dict[str, PlayerOutlook]:
        remaining = self._remaining_opponents()
        factors = self.defense_factors()
        team_sos = {
            pos: {team: _mean([factors[pos].get(opp, 1.0) for _, opp in games]) for team, games in remaining.items()}
            for pos in Position
        }
        ranks = {pos: _rank_easiest(team_sos[pos]) for pos in Position}
        form = self._recent_form()
        gsis_by_espn = self.gsis_by_espn()

        outlooks = {}
        for player in players:
            games = remaining.get(player.team, [])
            sos = team_sos[player.position].get(player.team) if games else None
            ppg, n_games = (None, 0)
            gsis = gsis_by_espn.get(player.espn_id) if player.espn_id else None
            if gsis and gsis in form:
                ppg, n_games = form[gsis]

            form_projection = None
            if (
                player.position != Position.DST
                and ppg is not None
                and n_games >= MIN_FORM_GAMES
                and player.injury_status not in FORM_EXCLUDED_INJURIES
            ):
                form_projection = ppg * len(games) * (sos or 1.0)
                low, high = (1 - FORM_CAP) * player.ros_projection, (1 + FORM_CAP) * player.ros_projection
                form_projection = min(max(form_projection, low), high)

            adjusted = player.ros_projection
            if form_projection is not None:
                adjusted = (1 - self.form_weight) * player.ros_projection + self.form_weight * form_projection

            outlooks[player.id] = PlayerOutlook(
                provider_projection=player.ros_projection,
                adjusted_projection=round(adjusted, 2),
                games_remaining=len(games),
                remaining_opponents=[opp for _, opp in games],
                sos_factor=round(sos, 3) if sos is not None else None,
                sos_rank=ranks[player.position].get(player.team) if games else None,
                sos_teams=len(ranks[player.position]),
                recent_ppg=round(ppg, 2) if ppg is not None else None,
                recent_games=n_games,
                form_projection=round(form_projection, 2) if form_projection is not None else None,
            )
        return outlooks

    # ---------- queries ----------

    def _remaining_opponents(self) -> dict[str, list[tuple[int, str]]]:
        """Team -> [(week, opponent)] for regular-season games not yet played."""
        rows = self.conn.execute(
            """SELECT week, away_team, home_team FROM games
               WHERE season = ? AND game_type = 'REG' AND week BETWEEN ? AND ?
                 AND (away_score IS NULL OR home_score IS NULL)
               ORDER BY week""",
            (self.season, self.start_week, self.end_week),
        ).fetchall()
        remaining: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for week, away, home in rows:
            if week == self.start_week and (away in self.played or home in self.played):
                continue
            remaining[away].append((week, home))
            remaining[home].append((week, away))
        return remaining

    def defense_factors(self) -> dict[Position, dict[str, float]]:
        """Position -> defense -> shrunk ratio of points allowed to league average (>1 = easier)."""
        allowed: dict[Position, dict[str, list[float]]] = {pos: defaultdict(list) for pos in Position}

        per_game: dict[tuple, float] = defaultdict(float)
        for position, opponent, week, points in self._weekly_points():
            per_game[(position, opponent, week)] += points
        for (position, opponent, week), points in per_game.items():
            allowed[position][opponent].append(points)

        # Defenses: opponent offense points scored per game.
        for away, home, away_score, home_score in self.conn.execute(
            """SELECT away_team, home_team, away_score, home_score FROM games
               WHERE season = ? AND game_type = 'REG' AND away_score IS NOT NULL AND home_score IS NOT NULL""",
            (self.season,),
        ):
            allowed[Position.DST][away].append(float(away_score))  # away offense vs home DST
            allowed[Position.DST][home].append(float(home_score))

        factors: dict[Position, dict[str, float]] = {}
        for position, by_team in allowed.items():
            averages = {team: _mean(points) for team, points in by_team.items() if points}
            if not averages:
                factors[position] = {}
                continue
            league = _mean(list(averages.values()))
            factors[position] = {}
            for team, avg in averages.items():
                if league <= 0 or avg <= 0:
                    ratio = 1.0
                elif position == Position.DST:
                    ratio = league / avg  # facing a low-scoring offense is easier
                else:
                    ratio = avg / league
                n = len(by_team[team])
                factors[position][team] = 1 + (ratio - 1) * n / (n + SOS_SHRINK_GAMES)
        return factors

    def _weekly_points(self):
        """(position, opponent, week, points) for every player-game this season."""
        rows = self.conn.execute(
            """SELECT position, opponent, week, fantasy_points, fantasy_points_ppr, stats
               FROM player_week_stats
               WHERE season = ? AND season_type = 'REG' AND opponent IS NOT NULL
                 AND position IN ('QB', 'RB', 'WR', 'TE', 'K')""",
            (self.season,),
        )
        for position, opponent, week, std, ppr, stats in rows:
            yield Position(position), opponent, week, self.points(position, std, ppr, stats)

    def _recent_form(self) -> dict[str, tuple[float, int]]:
        """gsis_id -> (average points over last FORM_GAMES games, games counted)."""
        latest = self.conn.execute(
            "SELECT max(week) FROM player_week_stats WHERE season = ? AND season_type = 'REG'", (self.season,)
        ).fetchone()[0]
        if latest is None:
            return {}
        rows = self.conn.execute(
            """SELECT gsis_id, position, fantasy_points, fantasy_points_ppr, stats FROM (
                   SELECT *, ROW_NUMBER() OVER (PARTITION BY gsis_id ORDER BY week DESC) AS rn
                   FROM player_week_stats
                   WHERE season = ? AND season_type = 'REG' AND week > ?
                     AND position IN ('QB', 'RB', 'WR', 'TE', 'K'))
               WHERE rn <= ?""",
            (self.season, latest - FORM_WINDOW_WEEKS, FORM_GAMES),
        )
        points: dict[str, list[float]] = defaultdict(list)
        for gsis, position, std, ppr, stats in rows:
            points[gsis].append(self.points(position, std, ppr, stats))
        return {gsis: (_mean(p), len(p)) for gsis, p in points.items()}

    def gsis_by_espn(self) -> dict[int, str]:
        return {
            espn_id: gsis
            for gsis, espn_id in self.conn.execute("SELECT gsis_id, espn_id FROM players WHERE espn_id IS NOT NULL")
        }

    def points(self, position: str, std: float | None, ppr: float | None, stats_json: str) -> float:
        if position == "K":
            return kicker_points(json.loads(stats_json))
        std, ppr = std or 0.0, ppr or 0.0
        if self.scoring == ScoringFormat.PPR:
            return ppr
        if self.scoring == ScoringFormat.STANDARD:
            return std
        return (std + ppr) / 2


def kicker_points(stats: dict) -> float:
    """Common default kicker scoring: FG 0-39 = 3, 40-49 = 4, 50+ = 5, PAT = 1, missed FG = -1."""
    def g(key: str) -> float:
        value = stats.get(key) or 0
        return float(value) if isinstance(value, (int, float)) else 0.0

    return (
        3 * (g("fg_made_0_19") + g("fg_made_20_29") + g("fg_made_30_39"))
        + 4 * g("fg_made_40_49")
        + 5 * (g("fg_made_50_59") + g("fg_made_60_"))
        + g("pat_made")
        - g("fg_missed")
    )


def schedule_label(rank: int | None, teams: int) -> str | None:
    if rank is None or teams == 0:
        return None
    if rank <= SCHEDULE_TIER_SIZE:
        return "easy"
    if rank > teams - SCHEDULE_TIER_SIZE:
        return "hard"
    return "average"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rank_easiest(sos_by_team: dict[str, float]) -> dict[str, int]:
    """1 = easiest remaining schedule (highest factor)."""
    ordered = sorted(sos_by_team, key=lambda team: sos_by_team[team], reverse=True)
    return {team: i + 1 for i, team in enumerate(ordered)}
