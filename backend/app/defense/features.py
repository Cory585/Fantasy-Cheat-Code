"""Pre-game features for predicting a defense's fantasy points.

Every feature for a game in week W uses only information available before kickoff:
season-to-date stats from weeks before W, that week's injury report, the closing Vegas
line, and game-time weather. Early in a season, season-to-date stats are thin, so each one
is blended with a prior (the previous season's value, or the head coach's previous season
for play-calling tendencies), weighted as if the prior were PRIOR_GAMES games of data.

Features (opponent = the offense the defense is facing):
    opp_implied_points     Vegas implied points for the opponent (from spread and total)
    opp_points_pg          opponent points per game
    opp_sack_rate          sacks taken per dropback by the opponent
    opp_turnover_rate      interceptions + fumbles lost per opponent play
    opp_epa_per_dropback   opponent passing efficiency
    opp_proe               opponent neutral pass rate over expected (coaching tendency)
    opp_plays_pg           opponent plays per game (pace)
    opp_qb_rating          season passer rating of the opponent's expected starting QB
    opp_qb_out             1 if the opponent's usual starting QB is out this week
    opp_receiver_depth     receiving yards per game of the opponent's top 3 available WR/TE
    opp_receivers_out      how many of the opponent's top 3 receivers are out this week
    def_sacks_pg           the defense's sacks per game
    def_takeaways_pg       the defense's interceptions + fumble recoveries per game
    def_dst_ppg            the defense's fantasy points per game
    def_points_allowed_pg  points the defense allows per game
    def_players_out        defensive players listed out this week
    home                   1 if the defense is at home
    wind_mph               game wind speed (0 indoors)
    cold                   degrees below 40F, in tens (0 indoors)
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from app.defense.scoring import dst_points

FEATURES = [
    "opp_implied_points", "opp_points_pg", "opp_sack_rate", "opp_turnover_rate", "opp_epa_per_dropback",
    "opp_proe", "opp_plays_pg", "opp_qb_rating", "opp_qb_out", "opp_receiver_depth", "opp_receivers_out",
    "def_sacks_pg", "def_takeaways_pg", "def_dst_ppg", "def_points_allowed_pg", "def_players_out",
    "home", "wind_mph", "cold",
]

# Direction each feature should push a defense's projection (+1 helps the defense).
EXPECTED_SIGNS = {
    "opp_implied_points": -1, "opp_points_pg": -1, "opp_sack_rate": +1, "opp_turnover_rate": +1,
    "opp_epa_per_dropback": -1, "opp_proe": +1, "opp_plays_pg": +1, "opp_qb_rating": -1, "opp_qb_out": +1,
    "opp_receiver_depth": -1, "opp_receivers_out": +1, "def_sacks_pg": +1, "def_takeaways_pg": +1,
    "def_dst_ppg": +1, "def_points_allowed_pg": -1, "def_players_out": -1, "home": +1, "wind_mph": +1, "cold": +1,
}

PRIOR_GAMES = 4.0  # how many games' worth of weight the prior gets
QB_PRIOR_ATTEMPTS = 150.0
OUT_STATUSES = {"Out", "Doubtful"}
INDOOR_ROOFS = {"dome", "closed"}
DEFENSIVE_POSITIONS = {"DE", "DT", "NT", "DL", "LB", "ILB", "OLB", "MLB", "CB", "S", "FS", "SS", "DB", "SAF"}
RECEIVER_POSITIONS = {"WR", "TE"}
DEFAULT_WIND = 8.0  # typical outdoor wind when a value is missing


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


def passer_rating(completions: float, attempts: float, yards: float, tds: float, ints: float) -> float:
    """NFL passer rating (0-158.3)."""
    if attempts <= 0:
        return 0.0
    def clamp(x):
        return max(0.0, min(2.375, x))
    a = clamp((completions / attempts - 0.3) * 5)
    b = clamp((yards / attempts - 3) * 0.25)
    c = clamp(tds / attempts * 20)
    d = clamp(2.375 - ints / attempts * 25)
    return (a + b + c + d) / 6 * 100


def implied_points(team_is_home: bool, spread: float | None, total: float | None) -> float | None:
    """nflverse spread_line is the expected home margin; total_line the expected combined score."""
    if spread is None or total is None:
        return None
    return (total + spread) / 2 if team_is_home else (total - spread) / 2


class DefenseFeatureBuilder:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._load()

    # ---------- loading ----------

    def _load(self) -> None:
        c = self.conn
        self.games = [dict(zip(
            ("game_id", "season", "week", "away", "home", "away_score", "home_score", "spread", "total",
             "roof", "temp", "wind", "espn_id", "away_coach", "home_coach"), row))
            for row in c.execute(
                """SELECT game_id, season, week, away_team, home_team, away_score, home_score, spread_line,
                          total_line, roof, temp, wind, espn_id, away_coach, home_coach
                   FROM games WHERE game_type = 'REG' ORDER BY season, week""")]

        # Points scored/allowed per team-game.
        self.scored = defaultdict(dict)  # (season, team) -> week -> points scored
        self.allowed = defaultdict(dict)
        self.coach = {}  # (season, team) -> head coach (from any game)
        for g in self.games:
            for team, coach in ((g["away"], g["away_coach"]), (g["home"], g["home_coach"])):
                if coach:
                    self.coach[(g["season"], team)] = coach
            if g["away_score"] is None or g["home_score"] is None:
                continue
            self.scored[(g["season"], g["away"])][g["week"]] = g["away_score"]
            self.scored[(g["season"], g["home"])][g["week"]] = g["home_score"]
            self.allowed[(g["season"], g["away"])][g["week"]] = g["home_score"]
            self.allowed[(g["season"], g["home"])][g["week"]] = g["away_score"]

        # Offense per game (play-calling, efficiency).
        self.offense = defaultdict(dict)  # (season, team) -> week -> row
        for row in c.execute(
            """SELECT season, week, team, plays, dropbacks, sacks, interceptions, fumbles_lost, dropback_epa,
                      pass_oe_sum, pass_oe_n FROM team_game_offense WHERE season_type = 'REG'"""):
            season, week, team, *values = row
            self.offense[(season, team)][week] = dict(zip(
                ("plays", "dropbacks", "sacks", "ints", "fumbles", "epa", "oe_sum", "oe_n"), values))

        # Defense stat lines.
        self.defense = defaultdict(dict)  # (season, team) -> week -> stats dict
        for season, week, team, stats in c.execute(
                "SELECT season, week, team, stats FROM team_week_stats WHERE season_type = 'REG'"):
            self.defense[(season, team)][week] = json.loads(stats)

        # QBs and receivers per game.
        self.qbs = defaultdict(lambda: defaultdict(dict))  # (season, team) -> week -> gsis -> stats
        self.receivers = defaultdict(lambda: defaultdict(dict))
        self.names = {}
        for gsis, season, week, team, position, name, stats in c.execute(
            """SELECT gsis_id, season, week, team, position, name, stats FROM player_week_stats
               WHERE season_type = 'REG' AND position IN ('QB', 'WR', 'TE')"""):
            s = json.loads(stats)
            self.names[gsis] = name
            if position == "QB":
                self.qbs[(season, team)][week][gsis] = {k: float(s.get(k) or 0) for k in
                    ("attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions")}
            else:
                self.receivers[(season, team)][week][gsis] = {
                    "targets": float(s.get("targets") or 0), "yards": float(s.get("receiving_yards") or 0)}

        # Injury reports: (season, week, team) -> gsis -> (position, status)
        self.injuries = defaultdict(dict)
        for season, week, team, gsis, position, status in c.execute(
                "SELECT season, week, team, gsis_id, position, report_status FROM injuries WHERE season_type = 'REG'"):
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

    def _ratio(self, season, team, week, num, den):
        rows = self._before(self.offense.get((season, team), {}), week)
        d = sum(r[den] for r in rows)
        return sum(r[num] for r in rows) / d if d else None

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

        # Opponent scoring and Vegas.
        f["opp_points_pg"] = self._blend(s, opponent, w, lambda s_, t, w_: self._per_game(self.scored, s_, t, w_, lambda x: x), "ppg")
        implied = implied_points(not is_home, g["spread"], g["total"])
        f["opp_implied_points"] = implied if implied is not None else f["opp_points_pg"]
        ctx["opp_implied_points"] = implied

        # Opponent offense tendencies and efficiency.
        f["opp_sack_rate"] = self._blend(s, opponent, w, lambda s_, t, w_: self._ratio(s_, t, w_, "sacks", "dropbacks"), "sack_rate")
        f["opp_turnover_rate"] = self._blend(
            s, opponent, w,
            lambda s_, t, w_: self._turnover_rate(s_, t, w_), "to_rate")
        f["opp_epa_per_dropback"] = self._blend(s, opponent, w, lambda s_, t, w_: self._ratio(s_, t, w_, "epa", "dropbacks"), "epa")
        f["opp_plays_pg"] = self._blend(s, opponent, w, lambda s_, t, w_: self._per_game(self.offense, s_, t, w_, lambda r: r["plays"]), "plays")
        f["opp_proe"], ctx["opp_coach"] = self._proe(s, opponent, w)

        # Opponent QB and receivers, adjusted for this week's injury report.
        report = self.injuries.get((s, w, opponent), {})
        f["opp_qb_rating"], f["opp_qb_out"], ctx["opp_qb"] = self._expected_qb(s, opponent, w, report)
        f["opp_receiver_depth"], f["opp_receivers_out"], ctx["opp_receivers_out"] = self._receivers(s, opponent, w, report)

        # The defense itself.
        def def_rate(fn):
            return lambda s_, t, w_: self._per_game(self.defense, s_, t, w_, fn)
        f["def_sacks_pg"] = self._blend(s, defense, w, def_rate(lambda d: float(d.get("def_sacks") or 0)), "sacks")
        f["def_takeaways_pg"] = self._blend(s, defense, w, def_rate(
            lambda d: float(d.get("def_interceptions") or 0) + float(d.get("fumble_recovery_opp") or 0)), "takeaways")
        f["def_points_allowed_pg"] = self._blend(
            s, defense, w, lambda s_, t, w_: self._per_game(self.allowed, s_, t, w_, lambda x: x), "allowed")
        f["def_dst_ppg"] = self._blend(s, defense, w, self._dst_ppg, "dst_ppg")
        own_report = self.injuries.get((s, w, defense), {})
        out_defenders = [gsis for gsis, (pos, status) in own_report.items()
                         if status in OUT_STATUSES and pos in DEFENSIVE_POSITIONS]
        f["def_players_out"] = float(len(out_defenders))

        # Venue and weather.
        f["home"] = 1.0 if is_home else 0.0
        f["wind_mph"], f["cold"], ctx["weather"] = self._weather(g)

        target = None
        if played:
            stats = self.defense.get((s, defense), {}).get(w)
            allowed = g["away_score"] if is_home else g["home_score"]
            if stats is not None:
                target = dst_points(stats, allowed)
        return FeatureRow(s, w, g["game_id"], g["espn_id"], defense, opponent, is_home, f, target, ctx)

    def _turnover_rate(self, season, team, week):
        rows = self._before(self.offense.get((season, team), {}), week)
        plays = sum(r["plays"] for r in rows)
        return sum(r["ints"] + r["fumbles"] for r in rows) / plays if plays else None

    def _dst_ppg(self, season, team, week):
        games = self._before(self.defense.get((season, team), {}), week)
        allowed = self.allowed.get((season, team), {})
        points = [dst_points(stats, allowed[w]) for w, stats in self.defense.get((season, team), {}).items()
                  if w < week and w in allowed]
        return sum(points) / len(points) if games and points else None

    def _proe(self, season, team, week):
        """Neutral pass rate over expected, with the head coach's last season as the prior."""
        rows = self._before(self.offense.get((season, team), {}), week)
        oe_sum, oe_n = sum(r["oe_sum"] for r in rows), sum(r["oe_n"] for r in rows)
        coach = self.coach.get((season, team))
        prior = 0.0  # league average PROE is ~0 by construction
        if coach:
            prev_team = next((t for (s_, t), c_ in self.coach.items() if s_ == season - 1 and c_ == coach), None)
            if prev_team:
                prev = list(self.offense.get((season - 1, prev_team), {}).values())
                n = sum(r["oe_n"] for r in prev)
                if n:
                    prior = sum(r["oe_sum"] for r in prev) / n
        prior_plays = PRIOR_GAMES * 35  # ~35 neutral plays a game
        return (oe_sum + prior * prior_plays) / (oe_n + prior_plays), coach

    def _expected_qb(self, season, team, week, report):
        """Passer rating of the QB expected to start, and whether the usual starter is out."""
        by_week = self.qbs.get((season, team), {})
        played = sorted(w for w in by_week if w < week)
        league = 90.0
        if not played:
            return league, 0.0, None
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
            return league - 15, 1.0, None  # unknown backup
        sums = defaultdict(float)
        for w in played:
            for k, v in by_week[w].get(expected, {}).items():
                sums[k] += v
        rating = passer_rating(sums["completions"], sums["attempts"], sums["passing_yards"],
                               sums["passing_tds"], sums["passing_interceptions"])
        attempts = sums["attempts"]
        blended = (rating * attempts + league * QB_PRIOR_ATTEMPTS) / (attempts + QB_PRIOR_ATTEMPTS)
        return blended, 1.0 if out else 0.0, self.names.get(expected)

    def _receivers(self, season, team, week, report):
        """Yards per game of the top 3 available WR/TE (by targets), and how many top-3 are out."""
        by_week = self.receivers.get((season, team), {})
        totals = defaultdict(lambda: {"targets": 0.0, "yards": 0.0, "games": 0})
        for w, players in by_week.items():
            if w < week:
                for gsis, st in players.items():
                    t = totals[gsis]
                    t["targets"] += st["targets"]
                    t["yards"] += st["yards"]
                    t["games"] += 1
        if not totals:
            return 150.0, 0.0, []
        ranked = sorted(totals, key=lambda g_: totals[g_]["targets"], reverse=True)
        out = [g_ for g_ in ranked[:3] if report.get(g_, (None, None))[1] in OUT_STATUSES]
        available = [g_ for g_ in ranked if g_ not in out][:3]
        depth = sum(totals[g_]["yards"] / totals[g_]["games"] for g_ in available)
        return depth, float(len(out)), [self.names.get(g_) for g_ in out]

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
