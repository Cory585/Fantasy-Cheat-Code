"""Pre-game features for predicting a team's kicker points.

Built the same way as the DST features (`app.defense.features`): every feature for week W
uses only what was known before kickoff, and season-to-date rates are blended with last
season's (PRIOR_GAMES games of weight). Rows are per team-game, and the target is the team's
kicker points that game (`kicker_points`: FG 0-39 = 3, 40-49 = 4, 50+ = 5, PAT = 1,
missed FG = -1), so a kicker who changes teams mid-season doesn't break the history.

Features (team = the kicking team, opponent = the defense it faces):
    team_implied_points    Vegas implied points for the team
    team_points_pg         team points per game
    team_fg_att_pg         field goal attempts per game
    team_pat_att_pg        extra point attempts per game
    team_fg_share          FG attempts / (FG attempts + offensive TDs): how often drives stall
                           into kicks instead of touchdowns
    team_plays_pg          offensive plays per game (more possessions, more chances)
    team_epa_per_dropback  passing efficiency (moving into range)
    team_k_ppg             kicker points per game
    fg_pct                 FG accuracy, blended with the league rate over FG_PRIOR_ATTEMPTS
    long_fg_share          share of FG attempts from 50+ (leg strength and coach aggression)
    opp_k_allowed_pg       kicker points the opponent allows per game
    opp_fg_att_allowed_pg  FG attempts the opponent allows per game (bend-don't-break defenses)
    opp_points_allowed_pg  points the opponent allows per game
    exp_k_points           expected kicker points: implied points x the team's kicker share of
                           its points, scaled by the opponent's kicker points allowed vs league
    indoor                 1 in a dome or closed roof
    wind_mph               game wind speed (0 indoors)
    cold                   degrees below 40F, in tens (0 indoors)
    altitude               1 at Denver
    home                   1 if the team is at home
"""

from app.defense.features import DefenseFeatureBuilder, FeatureRow, implied_points
from app.services.outlook import kicker_points

FEATURES = [
    "team_implied_points", "team_points_pg", "team_fg_att_pg", "team_pat_att_pg", "team_fg_share",
    "team_plays_pg", "team_epa_per_dropback", "team_k_ppg", "fg_pct", "long_fg_share",
    "opp_k_allowed_pg", "opp_fg_att_allowed_pg", "opp_points_allowed_pg", "exp_k_points",
    "indoor", "wind_mph", "cold", "altitude", "home",
]

# Direction each feature should push a kicker's projection.
EXPECTED_SIGNS = {
    "team_implied_points": +1, "team_points_pg": +1, "team_fg_att_pg": +1, "team_pat_att_pg": +1,
    "team_fg_share": +1, "team_plays_pg": +1, "team_epa_per_dropback": +1, "team_k_ppg": +1, "fg_pct": +1,
    "long_fg_share": +1, "opp_k_allowed_pg": +1, "opp_fg_att_allowed_pg": +1, "opp_points_allowed_pg": +1,
    "exp_k_points": +1, "indoor": +1, "wind_mph": -1, "cold": -1, "altitude": +1, "home": +1,
}

LEAGUE_FG_PCT = 0.85
FG_PRIOR_ATTEMPTS = 20.0
LEAGUE_K_POINTS = 8.0  # typical kicker points per game, the fallback for exp_k_points
LONG_BUCKETS = ("50_59", "60_")
HIGH_ALTITUDE_HOME = {"DEN"}


def _g(stats: dict, key: str) -> float:
    value = stats.get(key) or 0
    return float(value) if isinstance(value, (int, float)) else 0.0


def fg_attempts(stats: dict) -> float:
    return _g(stats, "fg_att")


def offensive_tds(stats: dict) -> float:
    return _g(stats, "passing_tds") + _g(stats, "rushing_tds")


def long_attempts(stats: dict) -> float:
    return sum(_g(stats, f"fg_made_{b}") + _g(stats, f"fg_missed_{b}") for b in LONG_BUCKETS)


class KickerFeatureBuilder(DefenseFeatureBuilder):
    """Reuses the DST builder's loaded games, team stat lines, offense, and weather.

    `self.defense` there holds every team's full stat line by week, kicking included."""

    def _load(self) -> None:
        super()._load()
        # (season, team) -> week -> the opponent's stat line in that game.
        self.lines_against = {}
        for g in self.games:
            for team, opp in ((g["away"], g["home"]), (g["home"], g["away"])):
                stats = self.defense.get((g["season"], opp), {}).get(g["week"])
                if stats is not None:
                    self.lines_against.setdefault((g["season"], team), {})[g["week"]] = stats

    def rows(self, seasons: list[int], include_unplayed: bool = False) -> list[FeatureRow]:
        out = []
        for g in self.games:
            if g["season"] not in seasons:
                continue
            played = g["away_score"] is not None and g["home_score"] is not None
            if not played and not include_unplayed:
                continue
            for team, opponent, is_home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
                out.append(self._kicker_row(g, team, opponent, is_home, played))
        return out

    # Sums over the team's stat lines before `week`, divided by games (or by another sum).
    def _stat_rate(self, num, den=None):
        def rate(season, team, week):
            rows = self._before(self.defense.get((season, team), {}), week)
            if not rows:
                return None
            total = sum(num(r) for r in rows)
            if den is None:
                return total / len(rows)
            d = sum(den(r) for r in rows)
            return total / d if d else None
        return rate

    def _against_rate(self, fn):
        """Per game, what opponents did against this team (from their stat lines)."""
        def rate(season, team, week):
            rows = self._before(self.lines_against.get((season, team), {}), week)
            return sum(fn(r) for r in rows) / len(rows) if rows else None
        return rate

    def _kicker_row(self, g, team, opponent, is_home, played) -> FeatureRow:
        s, w = g["season"], g["week"]
        f: dict[str, float] = {}

        def per_game(table):
            return lambda s_, t, w_: self._per_game(table, s_, t, w_, lambda x: x)

        f["team_points_pg"] = self._blend(s, team, w, per_game(self.scored), "ppg")
        implied = implied_points(is_home, g["spread"], g["total"])
        f["team_implied_points"] = implied if implied is not None else f["team_points_pg"]
        f["team_fg_att_pg"] = self._blend(s, team, w, self._stat_rate(fg_attempts), "fga")
        f["team_pat_att_pg"] = self._blend(s, team, w, self._stat_rate(lambda r: _g(r, "pat_att")), "pata")
        f["team_fg_share"] = self._blend(s, team, w, self._stat_rate(
            fg_attempts, lambda r: fg_attempts(r) + offensive_tds(r)), "fg_share")
        f["team_plays_pg"] = self._blend(s, team, w, lambda s_, t, w_: self._per_game(
            self.offense, s_, t, w_, lambda r: r["plays"]), "plays")
        f["team_epa_per_dropback"] = self._blend(s, team, w, lambda s_, t, w_: self._ratio(
            s_, t, w_, "epa", "dropbacks"), "epa")
        f["team_k_ppg"] = self._blend(s, team, w, self._stat_rate(kicker_points), "k_ppg")
        f["long_fg_share"] = self._blend(s, team, w, self._stat_rate(long_attempts, fg_attempts), "long_share")
        f["fg_pct"] = self._fg_pct(s, team, w)

        f["opp_k_allowed_pg"] = self._blend(s, opponent, w, self._against_rate(kicker_points), "k_allowed")
        f["opp_fg_att_allowed_pg"] = self._blend(s, opponent, w, self._against_rate(fg_attempts), "fga_allowed")
        f["opp_points_allowed_pg"] = self._blend(s, opponent, w, per_game(self.allowed), "allowed")

        # Expected kicker points: the kicker's usual share of team scoring, applied to this
        # game's implied points, scaled by how much this opponent gives up to kickers.
        share = f["team_k_ppg"] / f["team_points_pg"] if f["team_points_pg"] > 0 else LEAGUE_K_POINTS / 22
        league_allowed = self._league_avg(s - 1, "k_allowed", self._against_rate(kicker_points)) or LEAGUE_K_POINTS
        f["exp_k_points"] = f["team_implied_points"] * share * f["opp_k_allowed_pg"] / league_allowed

        wind, cold, weather = self._weather(g)
        f["indoor"] = 1.0 if weather.get("indoor") else 0.0
        f["wind_mph"], f["cold"] = wind, cold
        f["altitude"] = 1.0 if g["home"] in HIGH_ALTITUDE_HOME else 0.0
        f["home"] = 1.0 if is_home else 0.0

        target = None
        if played:
            stats = self.defense.get((s, team), {}).get(w)
            if stats is not None:
                target = kicker_points(stats)
        return FeatureRow(s, w, g["game_id"], g["espn_id"], team, opponent, is_home, f, target,
                          {"weather": weather, "team_implied_points": implied})

    def _fg_pct(self, season, team, week) -> float:
        """Season FG% (last season's as the prior), shrunk toward the league rate."""
        made = att = 0.0
        for s_, cutoff in ((season, week), (season - 1, 99)):
            for stats in self._before(self.defense.get((s_, team), {}), cutoff):
                att += fg_attempts(stats)
                made += fg_attempts(stats) - _g(stats, "fg_missed")
        return (made + LEAGUE_FG_PCT * FG_PRIOR_ATTEMPTS) / (att + FG_PRIOR_ATTEMPTS)
