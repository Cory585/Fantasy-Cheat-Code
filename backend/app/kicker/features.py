"""Pre-game features for predicting a team's kicker points.

Built the same way as the DST features (`app.defense.features`): every feature for week W
uses only what was known before kickoff, and season-to-date rates are blended with last
season's (PRIOR_GAMES games of weight). Rows are per team-game, and the target is the team's
kicker points that game (`kicker_points`: FG 0-39 = 3, 40-49 = 4, 50+ = 5, PAT = 1,
missed FG = -1), so a kicker who changes teams mid-season doesn't break the history.

Model features (team = the kicking team, opponent = the defense it faces):
    team_implied_points    Vegas implied points for the team (estimated from both teams' scoring
                           when there's no line yet, the same estimate the DST model uses)
    team_favored_by        points the team is favored by (favorites kick more: they take the
                           points late and run out the clock instead of chasing touchdowns)
    fg_pct                 FG accuracy, blended with the league rate over FG_PRIOR_ATTEMPTS
    long_fg_share          share of FG attempts from 50+ (leg strength and coach aggression)
    pat_pct                extra point accuracy, blended with the league rate over PAT_PRIOR_ATTEMPTS
    opp_k_allowed_pg       kicker points the opponent allows per game
    indoor                 1 in a dome or closed roof
    wind_mph               game wind speed (0 indoors)
    cold                   degrees below 40F, in tens (0 indoors)
    altitude               1 at Denver
    home                   1 if the team is at home

Rows also carry team_k_ppg (kicker points per game, a training baseline), which isn't a
model feature.

Tried and left out because they add nothing out of sample once the Vegas line is in (the
sign constraint dropped most of them anyway): team points, pace, passing efficiency, FG and
PAT attempts per game, how often drives stall into FGs (for the team and against the
opponent), the opponent's points and FG attempts allowed, and an expected-kicker-points
estimate that splits implied points into TD and FG drives.
"""

from app.defense.features import DefenseFeatureBuilder, FeatureRow, implied_points
from app.services.outlook import kicker_points

FEATURES = [
    "team_implied_points", "team_favored_by", "fg_pct", "long_fg_share", "pat_pct", "opp_k_allowed_pg",
    "indoor", "wind_mph", "cold", "altitude", "home",
]

# Direction each feature should push a kicker's projection.
EXPECTED_SIGNS = {
    "team_implied_points": +1, "team_favored_by": +1, "fg_pct": +1, "long_fg_share": +1, "pat_pct": +1,
    "opp_k_allowed_pg": +1, "indoor": +1, "wind_mph": -1, "cold": -1, "altitude": +1, "home": +1,
}

LEAGUE_FG_PCT = 0.85
FG_PRIOR_ATTEMPTS = 20.0
LEAGUE_PAT_PCT = 0.95
PAT_PRIOR_ATTEMPTS = 30.0
LONG_BUCKETS = ("50_59", "60_")
HIGH_ALTITUDE_HOME = {"DEN"}


def _g(stats: dict, key: str) -> float:
    value = stats.get(key) or 0
    return float(value) if isinstance(value, (int, float)) else 0.0


def fg_attempts(stats: dict) -> float:
    return _g(stats, "fg_att")


def long_attempts(stats: dict) -> float:
    return sum(_g(stats, f"fg_made_{b}") + _g(stats, f"fg_missed_{b}") for b in LONG_BUCKETS)


def _rate(rows: list, num, den=None):
    """Sum of `num` over `rows`, per row (game) or divided by the sum of `den`."""
    if not rows:
        return None
    total = sum(num(r) for r in rows)
    if den is None:
        return total / len(rows)
    d = sum(den(r) for r in rows)
    return total / d if d else None


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

    def dst_rows(self, seasons: list[int], include_unplayed: bool = False) -> list[FeatureRow]:
        """The DST model's rows, from the same loaded data (one load serves both models)."""
        return super().rows(seasons, include_unplayed)

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

    def _stat_rate(self, num, den=None):
        """The team's own stat lines before `week`: per game, or as a ratio of two sums."""
        return lambda season, team, week: _rate(self._before(self.defense.get((season, team), {}), week), num, den)

    def _against_rate(self, num):
        """Per game, what opponents did against this team (from their stat lines)."""
        return lambda season, team, week: _rate(self._before(self.lines_against.get((season, team), {}), week), num)

    def _kicker_row(self, g, team, opponent, is_home, played) -> FeatureRow:
        s, w = g["season"], g["week"]
        f: dict[str, float] = {}

        # Vegas, or the DST rows' estimate of the line when there isn't one yet. Training rows
        # almost all have closing lines, so raw points per game or a pick'em spread would put
        # later weeks on a different scale from what the model learned.
        implied = implied_points(is_home, g["spread"], g["total"])
        team_points = implied if implied is not None else self.estimated_points(s, w, team, opponent, is_home)
        if g["spread"] is not None:  # nflverse spread_line: the expected home margin
            favored = g["spread"] if is_home else -g["spread"]
        else:
            favored = team_points - self.estimated_points(s, w, opponent, team, not is_home)
        f["team_implied_points"] = team_points
        f["team_favored_by"] = float(favored)
        f["team_k_ppg"] = self._blend(s, team, w, self._stat_rate(kicker_points), "k_ppg")
        f["long_fg_share"] = self._blend(s, team, w, self._stat_rate(long_attempts, fg_attempts), "long_share")
        f["fg_pct"] = self._accuracy(s, team, w, fg_attempts, lambda r: fg_attempts(r) - _g(r, "fg_missed"),
                                     LEAGUE_FG_PCT, FG_PRIOR_ATTEMPTS)
        f["pat_pct"] = self._accuracy(s, team, w, lambda r: _g(r, "pat_att"), lambda r: _g(r, "pat_made"),
                                      LEAGUE_PAT_PCT, PAT_PRIOR_ATTEMPTS)
        f["opp_k_allowed_pg"] = self._blend(s, opponent, w, self._against_rate(kicker_points), "k_allowed")

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
                          {"weather": weather, "team_implied_points": implied, "line_estimated": implied is None})

    def _accuracy(self, season, team, week, attempts, made, league_rate, prior_attempts) -> float:
        """Make rate this season and last (attempts before `week`), shrunk toward the league rate."""
        made_total = att = 0.0
        for s_, cutoff in ((season, week), (season - 1, 99)):
            for stats in self._before(self.defense.get((s_, team), {}), cutoff):
                att += attempts(stats)
                made_total += made(stats)
        return (made_total + league_rate * prior_attempts) / (att + prior_attempts)
