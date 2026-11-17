"""Pre-game features for predicting a defense's fantasy points.

Every feature for a game in week W uses only information available before kickoff:
season-to-date stats from weeks before W, that week's injury report, the closing Vegas
line (estimated from both teams' scoring when there isn't one yet, see
`DefenseFeatureBuilder.estimated_points`), and game-time weather. Early in a season, season-to-date stats are thin, so each one
is blended with a prior (the previous season's value, or the head coach's previous season
for play-calling tendencies), weighted as if the prior were PRIOR_GAMES games of data.

Features (opponent = the offense the defense is facing):
    opp_implied_points     Vegas implied points for the opponent (from spread and total)
    opp_sack_rate          sacks taken per dropback by the opponent
    opp_proe               opponent neutral pass rate over expected (coaching tendency)
    opp_plays_pg           opponent plays per game (pace)
    opp_qb_sack_rate       sacks taken per dropback by the opponent's expected starting QB
    opp_qb_int_rate        interceptions per pass attempt by that QB (both adjusted for injuries)
    def_takeaways_pg       the defense's interceptions + fumble recoveries per game
    def_pressure_pg        the defense's QB hits per game (steadier than sacks)
    def_pass_defended_pg   passes defended per game (a steadier sign of interceptions to come)
    def_players_out        defensive players listed out this week
    def_favored_by         points the defense's team is favored by (trailing offenses pass more)
    wind_mph               game wind speed (0 indoors)

Matchup features combine both sides, because points come from the interaction and
points-allowed scoring is a step function:
    exp_pa_score           expected points-allowed score if the opponent scores around its implied
                           points (normal, sd TEAM_SCORE_SD)
    exp_takeaways          opponent plays per game x defense takeaway rate x opponent giveaway rate
                           / league giveaway rate

Earlier versions also used opponent scoring, turnover rate, passing efficiency, QB passer rating
and availability, and receiver injuries; the defense's sacks, sack rate, points allowed, fantasy
points per game, and pass defense EPA; expected sacks; rest; home; and cold. Once the Vegas line
is in, none of them improved the out-of-sample backtests, and the sign constraint was dropping
most of them anyway (they're correlated with the line and with each other).
"""

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.defense.scoring import dst_points, points_allowed_score

FEATURES = [
    "opp_implied_points", "opp_sack_rate", "opp_proe", "opp_plays_pg", "opp_qb_sack_rate", "opp_qb_int_rate",
    "def_takeaways_pg", "def_pressure_pg", "def_pass_defended_pg", "def_players_out", "def_favored_by",
    "wind_mph", "exp_pa_score", "exp_takeaways",
]

# Direction each feature should push a defense's projection (+1 helps the defense).
EXPECTED_SIGNS = {
    "opp_implied_points": -1, "opp_sack_rate": +1, "opp_proe": +1, "opp_plays_pg": +1, "opp_qb_sack_rate": +1,
    "opp_qb_int_rate": +1, "def_takeaways_pg": +1, "def_pressure_pg": +1, "def_pass_defended_pg": +1,
    "def_players_out": -1, "def_favored_by": +1, "wind_mph": +1, "exp_pa_score": +1, "exp_takeaways": +1,
}

PRIOR_GAMES = 4.0  # how many games' worth of weight the prior gets
QB_PRIOR_ATTEMPTS = 150.0
# League-typical QB rates, the prior for QBs with few attempts.
LEAGUE_QB_SACK_RATE = 0.068
LEAGUE_QB_INT_RATE = 0.023
LEAGUE_TURNOVER_RATE = 0.021  # interceptions + fumbles lost per offensive play
TEAM_SCORE_SD = 10.0  # how far a team's score typically lands from its implied points
OUT_STATUSES = {"Out", "Doubtful"}
INDOOR_ROOFS = {"dome", "closed"}
DEFENSIVE_POSITIONS = {"DE", "DT", "NT", "DL", "LB", "ILB", "OLB", "MLB", "CB", "S", "FS", "SS", "DB", "SAF"}
DEFAULT_WIND = 8.0  # typical outdoor wind when a value is missing
# Games without a Vegas line (more than a week out) get implied points from
#   intercept + a x team points per game + b x opponent points allowed per game + c x home,
# fit on the loaded games that have lines. These defaults, from 2019-2025 lines, are used when
# fewer than MIN_LINE_FIT_ROWS team-games are available to fit.
DEFAULT_LINE_COEFS = (-4.9, 0.67, 0.50, 1.6)
MIN_LINE_FIT_ROWS = 100


@dataclass
class FeatureRow:
    season: int
    week: int
    game_id: str
    espn_id: str | None
    defense: str
    opponent: str
    home: bool
    features: dict[str, float]
    target: float | None = None  # actual DST points, for games already played
    context: dict = field(default_factory=dict)  # human-readable details (QB name, weather, ...)


def implied_points(team_is_home: bool, spread: float | None, total: float | None) -> float | None:
    """nflverse spread_line is the expected home margin; total_line the expected combined score."""
    if spread is None or total is None:
        return None
    return (total + spread) / 2 if team_is_home else (total - spread) / 2


def expected_pa_score(implied: float, sd: float = TEAM_SCORE_SD) -> float:
    """Expected points-allowed score when the opponent's score is normal around `implied`.

    Scores are whole points, so each tier covers a range of integers; the normal is split at
    half-points. A tier step function of the mean alone would jump at tier boundaries."""
    def cdf(x: float) -> float:
        return 0.5 * (1 + math.erf((x - implied) / (sd * math.sqrt(2))))

    edges = [(0.5, 10), (6.5, 7), (13.5, 4), (20.5, 1), (27.5, 0), (34.5, -1)]
    total, below = 0.0, 0.0
    for edge, score in edges:
        p = cdf(edge)
        total += (p - below) * score
        below = p
    return total + (1 - below) * points_allowed_score(35)


class DefenseFeatureBuilder:
    def __init__(self, conn: sqlite3.Connection, first_season: int | None = None):
        """`first_season` skips loading older seasons (a season's features need only it and
        the season before), so building one season's rows for the API is quick."""
        self.conn = conn
        self.first_season = first_season or 0
        self._load()

    # ---------- loading ----------

    def _load(self) -> None:
        c = self.conn
        self.games = [dict(zip(
            ("game_id", "season", "week", "away", "home", "away_score", "home_score", "spread", "total",
             "roof", "temp", "wind", "espn_id", "away_coach", "home_coach", "gameday"), row))
            for row in c.execute(
                """SELECT game_id, season, week, away_team, home_team, away_score, home_score, spread_line,
                          total_line, roof, temp, wind, espn_id, away_coach, home_coach, gameday
                   FROM games WHERE game_type = 'REG' AND season >= ? ORDER BY season, week""",
                (self.first_season,))]

        # Points scored/allowed per team-game.
        self.scored = defaultdict(dict)  # (season, team) -> week -> points scored
        self.allowed = defaultdict(dict)
        # (season, team) -> week -> head coach that game. By week, so the weeks before a
        # mid-season firing keep the coach who was actually calling plays then.
        self.coaches = defaultdict(dict)
        for g in self.games:
            for team, coach in ((g["away"], g["away_coach"]), (g["home"], g["home_coach"])):
                if coach:
                    self.coaches[(g["season"], team)][g["week"]] = coach
            if g["away_score"] is None or g["home_score"] is None:
                continue
            self.scored[(g["season"], g["away"])][g["week"]] = g["away_score"]
            self.scored[(g["season"], g["home"])][g["week"]] = g["home_score"]
            self.allowed[(g["season"], g["away"])][g["week"]] = g["home_score"]
            self.allowed[(g["season"], g["home"])][g["week"]] = g["away_score"]

        # Offense per game (play-calling, efficiency).
        self.offense = defaultdict(dict)  # (season, team) -> week -> row
        self.offense_against = defaultdict(dict)  # (season, defense) -> week -> the opposing offense's row
        for row in c.execute(
            """SELECT season, week, team, opponent, plays, dropbacks, sacks, interceptions, fumbles_lost,
                      dropback_epa, pass_oe_sum, pass_oe_n FROM team_game_offense
               WHERE season_type = 'REG' AND season >= ?""", (self.first_season,)):
            season, week, team, opponent, *values = row
            offense = dict(zip(("plays", "dropbacks", "sacks", "ints", "fumbles", "epa", "oe_sum", "oe_n"),
                               (v or 0 for v in values)))
            self.offense[(season, team)][week] = offense
            if opponent:
                self.offense_against[(season, opponent)][week] = offense

        # Defense stat lines.
        self.defense = defaultdict(dict)  # (season, team) -> week -> stats dict
        for season, week, team, stats in c.execute(
                "SELECT season, week, team, stats FROM team_week_stats WHERE season_type = 'REG' AND season >= ?",
                (self.first_season,)):
            self.defense[(season, team)][week] = json.loads(stats)

        # QBs per game.
        self.qbs = defaultdict(lambda: defaultdict(dict))  # (season, team) -> week -> gsis -> stats
        self.names = {}
        for gsis, season, week, team, name, stats in c.execute(
            """SELECT gsis_id, season, week, team, name, stats FROM player_week_stats
               WHERE season_type = 'REG' AND position = 'QB' AND season >= ?""",
            (self.first_season,)):
            s = json.loads(stats)
            self.names[gsis] = name
            self.qbs[(season, team)][week][gsis] = {
                k: float(s.get(k) or 0) for k in ("attempts", "passing_interceptions", "sacks_suffered")}

        # Injury reports: (season, week, team) -> gsis -> (position, status)
        self.injuries = defaultdict(dict)
        for season, week, team, gsis, position, status in c.execute(
                "SELECT season, week, team, gsis_id, position, report_status FROM injuries "
                "WHERE season_type = 'REG' AND season >= ?", (self.first_season,)):
            if status:
                self.injuries[(season, week, team)][gsis] = (position, status)

        # Forecasts for upcoming games, by ESPN event id.
        self.forecasts = {}
        try:
            for espn_id, indoor, temp, wind in c.execute(
                    "SELECT espn_event_id, indoor, temperature_f, wind_mph FROM game_weather"):
                self.forecasts[str(espn_id)] = (indoor, temp, wind)
        except sqlite3.OperationalError:
            pass  # no weather collected yet

        self._league_cache = {}
        self._line_coefs = None
        self.line_fit_seasons = None  # seasons the line estimate is fit on (None: all loaded)

    # ---------- helpers ----------

    @staticmethod
    def _before(by_week: dict, week: int) -> list:
        return [v for w, v in by_week.items() if w < week]

    def _league_avg(self, season: int, key: str, fn) -> float:
        """League average of a full-season team value, used as a fallback prior."""
        cache_key = (season, key)
        if cache_key not in self._league_cache:
            values = [v for v in (fn(season, team, 99) for team in self._teams(season)) if v is not None]
            self._league_cache[cache_key] = sum(values) / len(values) if values else 0.0
        return self._league_cache[cache_key]

    def _teams(self, season: int) -> set[str]:
        return {team for (s, team) in self.scored if s == season}

    def _blend(self, season, team, week, rate_fn, key: str) -> float:
        """Season-to-date value blended with last season's (or the league average)."""
        current = rate_fn(season, team, week)
        n = self._games_before(season, team, week)
        prior = rate_fn(season - 1, team, 99)
        if prior is None:
            prior = self._league_avg(season - 1, key, rate_fn) if self._teams(season - 1) else (current or 0.0)
        if current is None:
            return prior
        return (current * n + prior * PRIOR_GAMES) / (n + PRIOR_GAMES)

    def _games_before(self, season, team, week) -> int:
        return len(self._before(self.scored.get((season, team), {}), week))

    # Per-game rates through `week` (exclusive). Return None with no games.
    def _per_game(self, table, season, team, week, fn):
        rows = self._before(table.get((season, team), {}), week)
        return sum(fn(r) for r in rows) / len(rows) if rows else None

    def _ratio(self, season, team, week, num, den, table=None):
        rows = self._before((self.offense if table is None else table).get((season, team), {}), week)
        d = sum(r[den] for r in rows)
        return sum(r[num] for r in rows) / d if d else None

    def _scoring(self, season, team, week) -> tuple[float, float]:
        """(points scored, points allowed) per game before `week`, blended with the prior."""
        return (
            self._blend(season, team, week, lambda s_, t, w_: self._per_game(self.scored, s_, t, w_, lambda x: x), "ppg"),
            self._blend(season, team, week, lambda s_, t, w_: self._per_game(self.allowed, s_, t, w_, lambda x: x), "allowed"),
        )

    def estimated_points(self, season, week, team, opponent, team_is_home) -> float:
        """Points `team` is expected to score against `opponent`, for games without a Vegas line:
        a linear fit of Vegas implied points on the team's scoring and the opponent's points
        allowed (see DEFAULT_LINE_COEFS). Raw points per game alone ignores the opponent and
        swings more than lines do, which made the model's biggest feature noisy for later weeks."""
        intercept, a, b, c = self.line_coefs()
        return (intercept + a * self._scoring(season, team, week)[0]
                + b * self._scoring(season, opponent, week)[1] + c * (1.0 if team_is_home else 0.0))

    def fit_lines_on(self, seasons) -> None:
        """Fit the line estimate only on games in `seasons` (None: every loaded season), e.g. a
        backtest's training seasons, so the test season's own lines don't shape its features."""
        self.line_fit_seasons = None if seasons is None else set(seasons)
        self._line_coefs = None

    def use_line_coefs(self, coefs) -> None:
        """Use fixed line-estimate coefficients (e.g. the ones a saved model was trained with)
        instead of fitting them on the loaded games. None goes back to fitting."""
        self._line_coefs = None if coefs is None else tuple(float(v) for v in coefs)

    def line_coefs(self) -> tuple[float, float, float, float]:
        if self._line_coefs is None:
            seasons_with_prior = {s for (s, team) in self.scored if (s - 1, team) in self.scored}
            if self.line_fit_seasons is not None:
                seasons_with_prior &= self.line_fit_seasons
            X, y = [], []
            for g in self.games:
                if g["season"] not in seasons_with_prior:
                    continue  # the first loaded season has no prior, so its early weeks are noise
                for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
                    implied = implied_points(home, g["spread"], g["total"])
                    if implied is not None:
                        X.append([1.0, self._scoring(g["season"], team, g["week"])[0],
                                  self._scoring(g["season"], opp, g["week"])[1], 1.0 if home else 0.0])
                        y.append(implied)
            if len(y) >= MIN_LINE_FIT_ROWS:
                self._line_coefs = tuple(float(v) for v in np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0])
            else:
                self._line_coefs = DEFAULT_LINE_COEFS
        return self._line_coefs

    # ---------- features ----------

    def rows(self, seasons: list[int], include_unplayed: bool = False) -> list[FeatureRow]:
        out = []
        for g in self.games:
            if g["season"] not in seasons:
                continue
            played = g["away_score"] is not None and g["home_score"] is not None
            if not played and not include_unplayed:
                continue
            for defense, opponent, is_home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
                out.append(self._row(g, defense, opponent, is_home, played))
        return out

    def _row(self, g, defense, opponent, is_home, played) -> FeatureRow:
        s, w = g["season"], g["week"]
        f: dict[str, float] = {}
        ctx: dict = {}

        def def_rate(fn):
            return lambda s_, t, w_: self._per_game(self.defense, s_, t, w_, fn)

        # Vegas, or an estimate of the line when there isn't one yet.
        implied = implied_points(not is_home, g["spread"], g["total"])
        ctx["opp_implied_points"] = implied
        ctx["line_estimated"] = implied is None
        if implied is None:
            implied = self.estimated_points(s, w, opponent, defense, not is_home)
        if g["spread"] is not None:
            favored = g["spread"] if is_home else -g["spread"]
        else:
            favored = self.estimated_points(s, w, defense, opponent, is_home) - implied
        f["opp_implied_points"] = implied
        f["def_favored_by"] = float(favored)

        # Opponent offense tendencies.
        f["opp_sack_rate"] = self._blend(s, opponent, w, lambda s_, t, w_: self._ratio(
            s_, t, w_, "sacks", "dropbacks"), "sack_rate")
        opp_turnover_rate = self._blend(s, opponent, w, lambda s_, t, w_: self._turnover_rate(s_, t, w_), "to_rate")
        f["opp_plays_pg"] = self._blend(s, opponent, w, lambda s_, t, w_: self._per_game(
            self.offense, s_, t, w_, lambda r: r["plays"]), "plays")
        f["opp_proe"], ctx["opp_coach"] = self._proe(s, opponent, w)

        # Opponent QB, adjusted for this week's injury report.
        report = self.injuries.get((s, w, opponent), {})
        (ctx["opp_qb_out"], ctx["opp_qb"],
         f["opp_qb_sack_rate"], f["opp_qb_int_rate"]) = self._expected_qb(s, opponent, w, report)

        # The defense itself.
        f["def_takeaways_pg"] = self._blend(s, defense, w, def_rate(
            lambda d: float(d.get("def_interceptions") or 0) + float(d.get("fumble_recovery_opp") or 0)), "takeaways")
        f["def_pressure_pg"] = self._blend(s, defense, w, def_rate(lambda d: float(d.get("def_qb_hits") or 0)), "qb_hits")
        f["def_pass_defended_pg"] = self._blend(
            s, defense, w, def_rate(lambda d: float(d.get("def_pass_defended") or 0)), "pass_defended")
        own_report = self.injuries.get((s, w, defense), {})
        out_defenders = [gsis for gsis, (pos, status) in own_report.items()
                         if status in OUT_STATUSES and pos in DEFENSIVE_POSITIONS]
        f["def_players_out"] = float(len(out_defenders))
        # Not a model feature: the "defense season average" baseline in the backtests.
        ctx["def_dst_ppg"] = self._blend(s, defense, w, self._dst_ppg, "dst_ppg")

        # Matchup interactions.
        f["exp_pa_score"] = expected_pa_score(f["opp_implied_points"])
        def_takeaway_rate = self._blend(s, defense, w, lambda s_, t, w_: self._turnover_rate(
            s_, t, w_, self.offense_against), "def_to_rate")
        f["exp_takeaways"] = f["opp_plays_pg"] * def_takeaway_rate * opp_turnover_rate / LEAGUE_TURNOVER_RATE

        f["wind_mph"], _, ctx["weather"] = self._weather(g)

        target = None
        if played:
            stats = self.defense.get((s, defense), {}).get(w)
            allowed = g["away_score"] if is_home else g["home_score"]
            if stats is not None:
                target = dst_points(stats, allowed, self._sacks_made(s, defense, w))
        return FeatureRow(s, w, g["game_id"], g["espn_id"], defense, opponent, is_home, f, target, ctx)

    def _sacks_made(self, season, defense, week) -> float | None:
        """Sacks by `defense` in that game from play-by-play (team sacks included), if collected."""
        game = self.offense_against.get((season, defense), {}).get(week)
        return None if game is None else game["sacks"]

    def _turnover_rate(self, season, team, week, table=None):
        rows = self._before((self.offense if table is None else table).get((season, team), {}), week)
        plays = sum(r["plays"] for r in rows)
        return sum(r["ints"] + r["fumbles"] for r in rows) / plays if plays else None

    def _dst_ppg(self, season, team, week):
        games = self._before(self.defense.get((season, team), {}), week)
        allowed = self.allowed.get((season, team), {})
        points = [dst_points(stats, allowed[w], self._sacks_made(season, team, w)) for w, stats in self.defense.get((season, team), {}).items()
                  if w < week and w in allowed]
        return sum(points) / len(points) if games and points else None

    def coach(self, season, team, week) -> str | None:
        """The team's head coach for its game in `week`, or its latest coach before then."""
        by_week = self.coaches.get((season, team), {})
        if week in by_week:
            return by_week[week]
        earlier = [w for w in by_week if w < week]
        return by_week[max(earlier)] if earlier else None

    def _proe(self, season, team, week):
        """Neutral pass rate over expected, with the head coach's last season as the prior."""
        rows = self._before(self.offense.get((season, team), {}), week)
        oe_sum, oe_n = sum(r["oe_sum"] for r in rows), sum(r["oe_n"] for r in rows)
        coach = self.coach(season, team, week)
        prior = 0.0  # league average PROE is ~0 by construction
        if coach:
            prev_team = next((t for (s_, t), by_week in self.coaches.items()
                              if s_ == season - 1 and coach in by_week.values()), None)
            if prev_team:
                prev = list(self.offense.get((season - 1, prev_team), {}).values())
                n = sum(r["oe_n"] for r in prev)
                if n:
                    prior = sum(r["oe_sum"] for r in prev) / n
        prior_plays = PRIOR_GAMES * 35  # ~35 neutral plays a game
        return (oe_sum + prior * prior_plays) / (oe_n + prior_plays), coach

    def _expected_qb(self, season, team, week, report):
        """(1 if the usual starter is out, name, sack rate, interception rate) of the QB expected
        to start. Rates are blended with league-typical ones over QB_PRIOR_ATTEMPTS."""
        by_week = self.qbs.get((season, team), {})
        played = sorted(w for w in by_week if w < week)
        if not played:
            return 0.0, None, LEAGUE_QB_SACK_RATE, LEAGUE_QB_INT_RATE
        last = by_week[played[-1]]
        starter = max(last, key=lambda q: last[q]["attempts"])
        out = report.get(starter, (None, None))[1] in OUT_STATUSES
        expected = starter
        if out:
            totals = defaultdict(float)
            for w in played:
                for q, st in by_week[w].items():
                    if q != starter and report.get(q, (None, None))[1] not in OUT_STATUSES:
                        totals[q] += st["attempts"]
            expected = max(totals, key=totals.get) if totals else None

        if expected is None:
            return 1.0, None, LEAGUE_QB_SACK_RATE, LEAGUE_QB_INT_RATE  # unknown backup
        sums = defaultdict(float)
        for w in played:
            for k, v in by_week[w].get(expected, {}).items():
                sums[k] += v
        attempts, sacks = sums["attempts"], sums["sacks_suffered"]
        sack_rate = (sacks + LEAGUE_QB_SACK_RATE * QB_PRIOR_ATTEMPTS) / (attempts + sacks + QB_PRIOR_ATTEMPTS)
        int_rate = (sums["passing_interceptions"] + LEAGUE_QB_INT_RATE * QB_PRIOR_ATTEMPTS) / (attempts + QB_PRIOR_ATTEMPTS)
        return 1.0 if out else 0.0, self.names.get(expected), sack_rate, int_rate

    def _weather(self, g):
        indoor = (g["roof"] or "").lower() in INDOOR_ROOFS
        temp, wind = g["temp"], g["wind"]
        forecast = self.forecasts.get(str(g["espn_id"])) if g["espn_id"] else None
        if forecast and g["away_score"] is None:
            f_indoor, f_temp, f_wind = forecast
            indoor = indoor or bool(f_indoor)
            temp, wind = f_temp, f_wind
        if indoor:
            return 0.0, 0.0, {"indoor": True}
        wind_value = float(wind) if wind is not None else DEFAULT_WIND
        cold = max(0.0, 40.0 - float(temp)) / 10 if temp is not None else 0.0
        return wind_value, cold, {"indoor": False, "temp_f": temp, "wind_mph": wind}
