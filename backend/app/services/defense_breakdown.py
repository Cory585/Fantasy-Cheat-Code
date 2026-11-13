"""Team defense breakdown: how each position group and unit ranks across the league.

Position groups (from each player's listed position in the weekly stats):
    DL  defensive line (DE, DT, NT, DL)
    LB  linebackers (LB, ILB, MLB, OLB)
    CB  cornerbacks (CB, DB)
    S   safeties (S, SAF, FS, SS)

A group's stats are summed over its players, week by week for the team they played for, and
divided by the team's games. Each group is ranked on a weighted average of z-scores (across
the league) of the stats that measure its job: sacks, QB hits, and tackles for loss for the
line; passes defended and interceptions for corners; and so on (GROUPS). Tackles count little:
lots of tackles can mean the offense keeps the ball, not that the defense is good.

Units are judged on results, from what opposing offenses did: pass rush (sack rate), coverage
(EPA per dropback and passing yards allowed), run defense (yards per carry), and scoring
(points allowed and DST fantasy points). Fantasy points allowed per position (PPR) come from
opposing players' weekly stats.
"""

import json
import logging
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev

from app.defense.scoring import dst_points
from app.models.defense import (
    DefenseBreakdown,
    DefenseLeague,
    FantasyAllowed,
    GroupPlayer,
    PositionGroup,
    RankedStat,
    TeamSummary,
    UnitResult,
    UpcomingGame,
)

log = logging.getLogger(__name__)

TOP_PLAYERS = 4
UPCOMING_GAMES = 3
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")


def _g(stats: dict, key: str) -> float:
    value = stats.get(key) or 0
    return float(value) if isinstance(value, (int, float)) else 0.0


# Player stats: key -> (label, value from a weekly stat line).
PLAYER_STATS = {
    "tackles": ("Tackles", lambda s: _g(s, "def_tackles_solo") + _g(s, "def_tackle_assists")),
    "tfl": ("Tackles for loss", lambda s: _g(s, "def_tackles_for_loss")),
    "sacks": ("Sacks", lambda s: _g(s, "def_sacks")),
    "qb_hits": ("QB hits", lambda s: _g(s, "def_qb_hits")),
    "pass_defended": ("Passes defended", lambda s: _g(s, "def_pass_defended")),
    "ints": ("Interceptions", lambda s: _g(s, "def_interceptions")),
    "forced_fumbles": ("Forced fumbles", lambda s: _g(s, "def_fumbles_forced")),
}


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    positions: frozenset[str]
    weights: dict[str, float]  # stat -> weight in the group's score
    shown: tuple[str, ...]  # stats listed for the group, most important first


GROUPS = (
    Group("DL", "Defensive line", frozenset({"DE", "DT", "NT", "DL"}),
          {"sacks": 1.0, "qb_hits": 0.75, "tfl": 0.75, "forced_fumbles": 0.5, "tackles": 0.1},
          ("sacks", "qb_hits", "tfl", "forced_fumbles", "tackles")),
    Group("LB", "Linebackers", frozenset({"LB", "ILB", "MLB", "OLB"}),
          {"tfl": 0.75, "sacks": 0.75, "pass_defended": 0.5, "ints": 0.5, "forced_fumbles": 0.5, "tackles": 0.25},
          ("tackles", "tfl", "sacks", "pass_defended", "ints", "forced_fumbles")),
    Group("CB", "Cornerbacks", frozenset({"CB", "DB"}),
          {"pass_defended": 1.0, "ints": 1.0, "forced_fumbles": 0.25, "tfl": 0.25, "tackles": 0.1},
          ("pass_defended", "ints", "forced_fumbles", "tfl", "tackles")),
    Group("S", "Safeties", frozenset({"S", "SAF", "FS", "SS"}),
          {"ints": 1.0, "pass_defended": 0.75, "forced_fumbles": 0.5, "tfl": 0.25, "tackles": 0.1},
          ("ints", "pass_defended", "forced_fumbles", "tfl", "tackles")),
)
DEFENSIVE_POSITIONS = frozenset().union(*(g.positions for g in GROUPS))

# Units: key -> (label, [(stat key, label, lower is better)]).
UNITS = {
    "pass_rush": ("Pass rush", [("sack_rate", "Sack rate", False), ("sacks_pg", "Sacks per game", False),
                                ("qb_hits_pg", "QB hits per game", False)]),
    "coverage": ("Coverage", [("epa_per_dropback", "EPA per dropback allowed", True),
                              ("pass_yards_pg", "Passing yards allowed per game", True),
                              ("int_rate", "Interception rate", False)]),
    "run_defense": ("Run defense", [("yards_per_carry", "Yards per carry allowed", True),
                                    ("rush_yards_pg", "Rushing yards allowed per game", True),
                                    ("rush_tds_pg", "Rushing TDs allowed per game", True)]),
    "scoring": ("Scoring defense", [("points_allowed_pg", "Points allowed per game", True),
                                    ("dst_ppg", "DST fantasy points per game", False),
                                    ("takeaways_pg", "Takeaways per game", False)]),
}


class NoDefenseData(Exception):
    """The database has no team stats for the requested season."""


def rank(values: dict[str, float], team: str, lower_is_better: bool = False) -> int:
    """1 + the number of teams strictly better (ties share a rank)."""
    mine = values[team]
    return 1 + sum(1 for v in values.values() if (v < mine if lower_is_better else v > mine))


def _zscores(values: dict[str, float]) -> dict[str, float]:
    sd = pstdev(values.values()) if len(values) > 1 else 0.0
    avg = mean(values.values())
    return {team: (v - avg) / sd if sd else 0.0 for team, v in values.items()}


@dataclass
class _TeamSeason:
    games: int = 0
    points_allowed: float = 0.0
    dst_points: float = 0.0
    sacks: float = 0.0
    qb_hits: float = 0.0
    takeaways: float = 0.0
    opp_dropbacks: float = 0.0
    opp_sacks: float = 0.0
    opp_ints: float = 0.0
    opp_dropback_epa: float = 0.0
    opp_pass_yards: float = 0.0
    opp_carries: float = 0.0
    opp_rush_yards: float = 0.0
    opp_rush_tds: float = 0.0


class LeagueDefenses:
    """Every team's breakdown for one season, computed once from the database."""

    def __init__(self, conn: sqlite3.Connection, season: int):
        self.season = season
        self._load(conn)
        if not self.teams:
            raise NoDefenseData(f"No team stats for the {season} season.")
        self._compute()

    # ---------- loading ----------

    def _load(self, conn: sqlite3.Connection) -> None:
        s = self.season
        lines: dict[tuple[str, int], dict] = {}
        opponents: dict[tuple[str, int], str] = {}
        for team, week, opponent, stats in conn.execute(
                "SELECT team, week, opponent, stats FROM team_week_stats WHERE season = ? AND season_type = 'REG'",
                (s,)):
            lines[(team, week)] = json.loads(stats)
            opponents[(team, week)] = opponent
        self.through_week = max((w for _, w in lines), default=0)

        allowed = {}
        for week, away, home, away_score, home_score in conn.execute(
                "SELECT week, away_team, home_team, away_score, home_score FROM games "
                "WHERE season = ? AND game_type = 'REG' AND away_score IS NOT NULL AND home_score IS NOT NULL", (s,)):
            allowed[(away, week)], allowed[(home, week)] = home_score, away_score

        offense_against: dict[tuple[str, int], tuple] = {}
        for opponent, week, dropbacks, sacks, ints, epa in conn.execute(
                "SELECT opponent, week, dropbacks, sacks, interceptions, dropback_epa FROM team_game_offense "
                "WHERE season = ? AND season_type = 'REG'", (s,)):
            if opponent:
                offense_against[(opponent, week)] = (dropbacks or 0, sacks or 0, ints or 0, epa or 0.0)

        self.teams: dict[str, _TeamSeason] = defaultdict(_TeamSeason)
        for (team, week), line in lines.items():
            if (team, week) not in allowed:
                continue
            t = self.teams[team]
            t.games += 1
            t.points_allowed += allowed[(team, week)]
            t.dst_points += dst_points(line, allowed[(team, week)])
            t.sacks += _g(line, "def_sacks")
            t.qb_hits += _g(line, "def_qb_hits")
            t.takeaways += _g(line, "def_interceptions") + _g(line, "fumble_recovery_opp")
            opp_line = lines.get((opponents[(team, week)], week), {})
            t.opp_pass_yards += _g(opp_line, "passing_yards")
            t.opp_carries += _g(opp_line, "carries")
            t.opp_rush_yards += _g(opp_line, "rushing_yards")
            t.opp_rush_tds += _g(opp_line, "rushing_tds")
            dropbacks, sacks, ints, epa = offense_against.get((team, week), (0, 0, 0, 0.0))
            t.opp_dropbacks += dropbacks
            t.opp_sacks += sacks
            t.opp_ints += ints
            t.opp_dropback_epa += epa
        self.teams = dict(self.teams)

        # Players: (team, group) -> gsis -> totals; credited to the team they played for that week.
        self.players: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        group_of = {pos: g.key for g in GROUPS for pos in g.positions}
        placeholders = ",".join("?" * len(DEFENSIVE_POSITIONS))
        for gsis, team, name, position, stats in conn.execute(
                f"SELECT gsis_id, team, name, position, stats FROM player_week_stats "
                f"WHERE season = ? AND season_type = 'REG' AND position IN ({placeholders})",
                (s, *sorted(DEFENSIVE_POSITIONS))):
            if team not in self.teams:
                continue
            line = json.loads(stats)
            entry = self.players[(team, group_of[position])].setdefault(
                gsis, {"name": name, "position": position, "games": 0, "stats": dict.fromkeys(PLAYER_STATS, 0.0)})
            entry["games"] += 1
            for key, (_, fn) in PLAYER_STATS.items():
                entry["stats"][key] += fn(line)

        self.fantasy_allowed: dict[str, dict[str, float]] = {pos: defaultdict(float) for pos in FANTASY_POSITIONS}
        for opponent, position, points in conn.execute(
                "SELECT opponent, position, SUM(fantasy_points_ppr) FROM player_week_stats "
                "WHERE season = ? AND season_type = 'REG' AND position IN ('QB', 'RB', 'WR', 'TE') "
                "GROUP BY opponent, position", (s,)):
            if opponent in self.teams:
                self.fantasy_allowed[position][opponent] = float(points or 0.0)

        self.info: dict[str, tuple] = {}
        try:
            for abbrev, name, logo, color in conn.execute(
                    "SELECT abbrev, display_name, logo_url, color FROM teams"):
                self.info[abbrev] = (name, logo, color)
        except sqlite3.OperationalError:
            pass  # teams not collected

    # ---------- league-wide numbers ----------

    def _compute(self) -> None:
        teams = self.teams
        # Group per-game stats: group -> stat -> team -> value.
        self.group_stats: dict[str, dict[str, dict[str, float]]] = {}
        self.group_scores: dict[str, dict[str, float]] = {}
        for group in GROUPS:
            per_game = {key: {} for key in PLAYER_STATS}
            for team, t in teams.items():
                players = self.players.get((team, group.key), {})
                for key in PLAYER_STATS:
                    per_game[key][team] = sum(p["stats"][key] for p in players.values()) / max(t.games, 1)
            z = {key: _zscores(per_game[key]) for key in group.weights}
            total = sum(group.weights.values())
            self.group_stats[group.key] = per_game
            self.group_scores[group.key] = {
                team: sum(w * z[key][team] for key, w in group.weights.items()) / total for team in teams}
        self.overall = {team: mean(self.group_scores[g.key][team] for g in GROUPS) for team in teams}

        def ratio(num, den):
            return {team: getattr(t, num) / getattr(t, den) if getattr(t, den) else 0.0 for team, t in teams.items()}

        def per_game(attr):
            return {team: getattr(t, attr) / max(t.games, 1) for team, t in teams.items()}

        self.unit_stats = {
            "sack_rate": ratio("opp_sacks", "opp_dropbacks"),
            "sacks_pg": per_game("sacks"),
            "qb_hits_pg": per_game("qb_hits"),
            "epa_per_dropback": ratio("opp_dropback_epa", "opp_dropbacks"),
            "pass_yards_pg": per_game("opp_pass_yards"),
            "int_rate": ratio("opp_ints", "opp_dropbacks"),
            "yards_per_carry": ratio("opp_rush_yards", "opp_carries"),
            "rush_yards_pg": per_game("opp_rush_yards"),
            "rush_tds_pg": per_game("opp_rush_tds"),
            "points_allowed_pg": per_game("points_allowed"),
            "dst_ppg": per_game("dst_points"),
            "takeaways_pg": per_game("takeaways"),
        }
        self.fantasy_pg = {pos: {team: self.fantasy_allowed[pos].get(team, 0.0) / max(t.games, 1)
                                 for team, t in teams.items()} for pos in FANTASY_POSITIONS}

    # ---------- output ----------

    def summary(self, team: str) -> TeamSummary:
        name, logo, color = self.info.get(team, (None, None, None))
        units = self.unit_stats
        return TeamSummary(
            team=team, name=name, logo_url=logo, color=color, games=self.teams[team].games,
            overall_rank=rank(self.overall, team),
            group_ranks={g.key: rank(self.group_scores[g.key], team) for g in GROUPS},
            points_allowed_pg=round(units["points_allowed_pg"][team], 1),
            points_allowed_rank=rank(units["points_allowed_pg"], team, lower_is_better=True),
            dst_ppg=round(units["dst_ppg"][team], 2),
            dst_rank=rank(units["dst_ppg"], team),
        )

    def league(self) -> DefenseLeague:
        summaries = sorted((self.summary(team) for team in self.teams), key=lambda s: (s.overall_rank, s.team))
        return DefenseLeague(season=self.season, through_week=self.through_week, teams=summaries)

    def breakdown(self, team: str, upcoming: list[UpcomingGame] | None = None) -> DefenseBreakdown:
        n = len(self.teams)
        groups = []
        for group in GROUPS:
            per_game = self.group_stats[group.key]
            stats = [RankedStat(key=key, label=PLAYER_STATS[key][0], value=round(per_game[key][team], 2),
                                rank=rank(per_game[key], team), teams=n) for key in group.shown]
            players = self.players.get((team, group.key), {})

            def impact(p, weights=group.weights):
                return sum(w * p["stats"][k] for k, w in weights.items())

            top = sorted(players.items(), key=lambda kv: (-impact(kv[1]), kv[1]["name"] or ""))[:TOP_PLAYERS]
            groups.append(PositionGroup(
                key=group.key, label=group.label, rank=rank(self.group_scores[group.key], team), teams=n,
                score=round(self.group_scores[group.key][team], 3), stats=stats,
                players=[GroupPlayer(gsis_id=gsis, name=p["name"] or gsis, position=p["position"], games=p["games"],
                                     stats={k: round(p["stats"][k], 1) for k in group.shown})
                         for gsis, p in top if impact(p) > 0],
            ))
        units = [
            UnitResult(key=key, label=label, stats=[
                RankedStat(key=stat, label=stat_label, value=round(self.unit_stats[stat][team], 4),
                           rank=rank(self.unit_stats[stat], team, lower), teams=n, lower_is_better=lower)
                for stat, stat_label, lower in stats])
            for key, (label, stats) in UNITS.items()
        ]
        fantasy = [FantasyAllowed(position=pos, points_per_game=round(self.fantasy_pg[pos][team], 2),
                                  rank=rank(self.fantasy_pg[pos], team, lower_is_better=True), teams=n)
                   for pos in FANTASY_POSITIONS]
        return DefenseBreakdown(season=self.season, through_week=self.through_week, summary=self.summary(team),
                                groups=groups, units=units, fantasy_allowed=fantasy, upcoming=upcoming or [])


def upcoming_games(matchups, team: str, from_week: int, count: int = UPCOMING_GAMES) -> list[UpcomingGame]:
    """The team's next games from `from_week`, with the DST model's projection and its rank among
    that week's defenses (when the model is trained)."""
    dst = matchups.team_models.dst
    games = []
    for week in sorted(w for w in matchups.schedule.get(team, {}) if w >= from_week):
        game = matchups.schedule[team][week]
        if game.played:
            continue
        estimate = dst.get(team, {}).get(week)
        week_points = {t: by_week[week].points for t, by_week in dst.items() if week in by_week}
        games.append(UpcomingGame(
            week=week, opponent=game.opponent, home=game.home,
            projection=estimate.points if estimate else None,
            rank=rank(week_points, team) if estimate else None,
            teams=len(week_points) if estimate else 0,
            factors=list(estimate.factors) if estimate else [],
        ))
        if len(games) == count:
            break
    return games


class DefenseBreakdowns:
    """Builds the league's defense breakdowns from `fantasy.db` (read-only) and keeps them until
    the database changes."""

    def __init__(self, database_path: Path | None):
        self._path = database_path
        self._lock = threading.Lock()
        self._cache: dict[int | None, tuple[tuple, LeagueDefenses]] = {}

    def _version(self) -> tuple:
        paths = (self._path, self._path.with_name(self._path.name + "-wal"))
        return tuple(p.stat().st_mtime if p.exists() else None for p in paths)

    def latest_season(self, conn: sqlite3.Connection) -> int | None:
        return conn.execute("SELECT MAX(season) FROM team_week_stats WHERE season_type = 'REG'").fetchone()[0]

    def get(self, season: int | None = None) -> LeagueDefenses:
        """The season's breakdowns (default: the latest season with team stats)."""
        if not self._path or not self._path.exists():
            raise NoDefenseData("No collected database. Run `python -m app.data.collect`.")
        with self._lock:
            version = self._version()
            cached = self._cache.get(season)
            if cached and cached[0] == version:
                return cached[1]
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=2)
            try:
                target = season or self.latest_season(conn)
                if target is None:
                    raise NoDefenseData("No team stats collected. Run `python -m app.data.collect`.")
                league = LeagueDefenses(conn, target)
            except sqlite3.Error as error:
                log.error("Couldn't build defense breakdowns from %s: %s", self._path, error)
                raise NoDefenseData("The database couldn't be read.") from None
            finally:
                conn.close()
            # Read the version again: opening a WAL database can create its -wal file.
            self._cache[season] = (self._version(), league)
            return league
