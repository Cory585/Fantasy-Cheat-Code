"""Player tags for quick lookup: roles (slot receiver, workhorse back, rushing QB), usage
(high target share, red-zone target, full-time snaps), trends (hot streak, boom or bust),
and status (rookie, injury, bye week, schedule).

Stat-based tags come from the current season's regular-season games in the collected
database, and only once a player has enough of a sample (MIN_GAMES games, and
MIN_TARGETS targets for the receiving-style tags). They're built when the data reloads.
Status tags come from the player pool and outlooks, so they're added per request.

Receiver alignment (slot or outside) isn't in any public data, so it's estimated from
where and how deep a receiver is targeted: slot receivers are thrown to between the
numbers and short; outside receivers are thrown to near the sidelines and deeper. Known
slot and outside receivers sort this way, but a receiver who moves around may get neither.
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean, pstdev

from app.models.player import Player, PlayerOutlook, Position
from app.models.tags import PlayerTag, TagCategory, TagDefinition, TagIndex, TagSummary
from app.models.trade import ScoringFormat

QB, RB, WR, TE, K, DST = (Position.QB, Position.RB, Position.WR, Position.TE, Position.K, Position.DST)
SKILL = [RB, WR, TE]
OFFENSE = [QB, RB, WR, TE]
EVERYONE = list(Position)

MIN_GAMES = 3
MIN_TARGETS = 15
RECENT_GAMES = 3  # for hot/cold streaks and rising snaps
TRENDING_CHANGE = 0.25  # ESPN's daily change in rostered %, in points
INJURY_STATUSES = {"Q": "Questionable", "D": "Doubtful", "O": "Out", "IR": "Injured reserve",
                   "PUP": "PUP list", "SUS": "Suspended", "NA": "Not active"}


def _tag(key, label, category, positions, description):
    return TagDefinition(key=key, label=label, category=category, positions=positions, description=description)


ROLE, USAGE, TREND, STATUS = TagCategory.ROLE, TagCategory.USAGE, TagCategory.TREND, TagCategory.STATUS

# In display order. Thresholds are in the descriptions so the page can explain each tag.
CATALOG: list[TagDefinition] = [
    # Receivers
    _tag("slot", "Slot receiver", ROLE, [WR],
         "Lines up inside, estimated from targets: a quarter or more thrown between the numbers at an "
         "average depth of 10.5 yards or less, 18%+ inside at 8.5 yards or less, or a very short game "
         "(6.5 yards or less)."),
    _tag("outside", "Outside receiver", ROLE, [WR],
         "Lines up wide, estimated from targets: no more than 20% between the numbers, with an average "
         "depth of 10 yards or more."),
    _tag("deep_threat", "Deep threat", ROLE, [WR, TE],
         "Average target depth of 13.5+ yards, or a quarter of targets thrown 20+ yards downfield."),
    _tag("possession", "Possession receiver", ROLE, [WR, TE],
         "Catches 70%+ of targets at an average depth of 7 to 11 yards, and moves the chains."),
    _tag("yac", "After-the-catch threat", ROLE, [WR, TE, RB],
         "Gains 6+ yards after the catch per reception (5+ for running backs is normal, so 8+ for them)."),
    _tag("screen", "Screen and short-area target", ROLE, [WR, TE],
         "A quarter or more of targets at or behind the line of scrimmage."),
    _tag("alpha", "Alpha receiver", ROLE, [WR, TE],
         "Commands 27%+ of the team's targets: the clear first read."),
    _tag("receiving_te", "Receiving tight end", ROLE, [TE],
         "Gets 17%+ of the team's targets, used more as a receiver than a blocker."),
    # Backs
    _tag("workhorse", "Workhorse back", ROLE, [RB],
         "Handles 60%+ of the team's carries and plays 60%+ of snaps."),
    _tag("committee", "Committee back", ROLE, [RB],
         "Splits the backfield: 30% to 55% of the team's carries."),
    _tag("pass_catching_back", "Pass-catching back", ROLE, [RB],
         "Sees 4+ targets a game: valuable in PPR."),
    _tag("goal_line_back", "Goal-line back", ROLE, [RB, QB],
         "Gets the carries inside the 5: 0.8+ a game, or half the team's goal-line carries (5+ in all)."),
    # Quarterbacks
    _tag("rushing_qb", "Rushing QB", ROLE, [QB], "Runs for 30+ yards a game: a fantasy floor with his legs."),
    _tag("gunslinger", "Gunslinger", ROLE, [QB], "Throws deep: average target depth of 8.5+ yards."),
    _tag("high_volume_passer", "High-volume passer", ROLE, [QB], "Throws 36+ passes a game."),
    # Usage
    _tag("high_target_share", "High target share", USAGE, SKILL,
         "Gets 22%+ of the team's targets (13%+ for running backs)."),
    _tag("air_yards_share", "Air-yards leader", USAGE, [WR, TE],
         "Gets 33%+ of the team's air yards: the downfield looks."),
    _tag("red_zone_target", "Red-zone target", USAGE, SKILL, "Gets 1.2+ targets a game inside the 20."),
    _tag("end_zone_target", "End-zone target", USAGE, SKILL, "Gets 0.8+ targets a game thrown into the end zone."),
    _tag("red_zone_back", "Red-zone carries", USAGE, [RB, QB], "Gets 2.5+ carries a game inside the 20."),
    _tag("third_down", "Third-down target", USAGE, SKILL, "Gets 2.5+ targets a game on third and fourth down."),
    _tag("full_time", "Full-time snaps", USAGE, SKILL, "Plays 80%+ of the offense's snaps."),
    _tag("part_time", "Part-time role", USAGE, SKILL, "Plays 20% to 50% of the offense's snaps: a rotational role."),
    _tag("snaps_rising", "Snaps rising", USAGE, SKILL,
         "Snap share over the last 3 games is 15+ points above earlier in the season, and at least half the snaps."),
    # Trends
    _tag("hot", "Hot streak", TREND, OFFENSE + [K], "Last 3 games average 25%+ more points than his season."),
    _tag("cold", "Cold streak", TREND, OFFENSE + [K], "Last 3 games average 30%+ fewer points than his season."),
    _tag("boom_bust", "Boom or bust", TREND, OFFENSE, "Week-to-week points swing widely (spread of 65%+ of his average)."),
    _tag("steady", "Steady producer", TREND, OFFENSE, "10+ points a game with little week-to-week swing."),
    _tag("td_dependent", "TD-dependent", TREND, SKILL, "40%+ of his points come from touchdowns."),
    _tag("trending_adds", "Trending adds", TREND, EVERYONE, "Being added in ESPN leagues: rostered rate up 0.25+ points in the latest daily snapshot."),
    # Status
    _tag("rookie", "Rookie", STATUS, OFFENSE + [K], "First NFL season."),
    _tag("injury", "Injury designation", STATUS, OFFENSE + [K], "On the injury report (questionable, doubtful, out, IR)."),
    _tag("on_bye", "On bye this week", STATUS, EVERYONE, "His team doesn't play this week."),
    _tag("easy_schedule", "Easy schedule ahead", STATUS, EVERYONE, "One of the 5 easiest remaining schedules at his position."),
    _tag("tough_schedule", "Tough schedule ahead", STATUS, EVERYONE, "One of the 5 hardest remaining schedules at his position."),
]
BY_KEY = {tag.key: tag for tag in CATALOG}
ORDER = {tag.key: i for i, tag in enumerate(CATALOG)}


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _per_game(x: float) -> str:
    return f"{x:.1f}"


@dataclass
class SeasonLine:
    """A player's regular season so far, summed across games."""

    position: str
    games: int = 0
    points: list[float] = field(default_factory=list)
    td_points: float = 0.0
    targets: int = 0
    receptions: int = 0
    yac: float = 0.0
    receiving_first_downs: int = 0
    target_share: list[float] = field(default_factory=list)
    air_yards_share: list[float] = field(default_factory=list)
    carries: int = 0
    team_carries: int = 0
    rushing_yards: float = 0.0
    attempts: int = 0
    passing_air_yards: float = 0.0
    # From play-by-play usage.
    pbp_targets: int = 0
    middle: int = 0
    air_yards: float = 0.0
    deep: int = 0
    behind_los: int = 0
    red_zone_targets: int = 0
    end_zone_targets: int = 0
    third_down_targets: int = 0
    red_zone_carries: int = 0
    goal_line_carries: int = 0
    team_goal_line_carries: int = 0
    # Snap share by week, in week order.
    snaps: list[float] = field(default_factory=list)


class TagBuilder:
    """Builds stat-based tags from the collected database for one season."""

    def __init__(self, conn: sqlite3.Connection, season: int, scoring: ScoringFormat = ScoringFormat.PPR):
        self.conn = conn
        self.season = season
        self.scoring = scoring

    def build(self, players: list[Player]) -> dict[str, list[PlayerTag]]:
        gsis_by_espn = dict(self.conn.execute(
            "SELECT espn_id, gsis_id FROM players WHERE espn_id IS NOT NULL").fetchall())
        lines = self._season_lines()
        rookies = self._rookies()
        trending = self._trending()
        tags: dict[str, list[PlayerTag]] = {}
        for player in players:
            found: list[PlayerTag] = []
            gsis = gsis_by_espn.get(player.espn_id) if player.espn_id else None
            line = lines.get(gsis) if gsis else None
            if line and line.games >= MIN_GAMES:
                found += player_stat_tags(player.position, line)
            if gsis in rookies and player.position not in (DST,):
                found.append(PlayerTag(key="rookie"))
            if player.espn_id in trending:
                found.append(PlayerTag(key="trending_adds", detail=f"Rostered rate +{trending[player.espn_id]:.2f} points today"))
            if found:
                tags[player.id] = found
        return tags

    # ---------- Queries ----------

    def _points(self, std, ppr) -> float:
        std, ppr = std or 0.0, ppr or 0.0
        if self.scoring == ScoringFormat.PPR:
            return ppr
        if self.scoring == ScoringFormat.STANDARD:
            return std
        return (std + ppr) / 2

    def _season_lines(self) -> dict[str, SeasonLine]:
        lines: dict[str, SeasonLine] = {}
        team_carries: dict[tuple[str, int], int] = defaultdict(int)
        played: dict[str, list[tuple[str, int]]] = defaultdict(list)
        rows = self.conn.execute(
            """SELECT gsis_id, position, team, week, fantasy_points, fantasy_points_ppr, stats
               FROM player_week_stats WHERE season = ? AND season_type = 'REG' ORDER BY week""",
            (self.season,),
        ).fetchall()
        for gsis, position, team, week, std, ppr, stats_json in rows:
            try:
                s = json.loads(stats_json)
            except (TypeError, ValueError):
                continue
            g = lambda key: s.get(key) or 0  # noqa: E731
            team_carries[(team, week)] += g("carries")
            line = lines.setdefault(gsis, SeasonLine(position=position))
            played[gsis].append((team, week))
            line.games += 1
            line.points.append(self._points(std, ppr))
            line.td_points += 6 * (g("rushing_tds") + g("receiving_tds")) + 4 * g("passing_tds")
            line.targets += g("targets")
            line.receptions += g("receptions")
            line.yac += g("receiving_yards_after_catch")
            line.receiving_first_downs += g("receiving_first_downs")
            if g("targets"):
                line.target_share.append(g("target_share"))
                line.air_yards_share.append(g("air_yards_share"))
            line.carries += g("carries")
            line.rushing_yards += g("rushing_yards")
            line.attempts += g("attempts")
            line.passing_air_yards += g("passing_air_yards")
        for gsis, games in played.items():
            lines[gsis].team_carries = sum(team_carries[game] for game in games)
        self._add_usage(lines)
        self._add_snaps(lines)
        return lines

    def _add_usage(self, lines: dict[str, SeasonLine]) -> None:
        try:
            rows = self.conn.execute(
                """SELECT gsis_id, team, week, targets, targets_middle, air_yards, deep_targets, behind_los_targets,
                          red_zone_targets, end_zone_targets, third_down_targets, red_zone_carries, goal_line_carries
                   FROM player_game_usage WHERE season = ? AND season_type = 'REG'""",
                (self.season,),
            ).fetchall()
        except sqlite3.OperationalError:
            return  # collected before usage existed
        team_gl: dict[tuple[str, int], int] = defaultdict(int)
        games: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for gsis, team, week, tg, mid, air, deep, behind, rz, ez, third, rzc, glc in rows:
            team_gl[(team, week)] += glc or 0
            line = lines.get(gsis)
            if line is None:
                continue
            games[gsis].append((team, week))
            line.pbp_targets += tg or 0
            line.middle += mid or 0
            line.air_yards += air or 0
            line.deep += deep or 0
            line.behind_los += behind or 0
            line.red_zone_targets += rz or 0
            line.end_zone_targets += ez or 0
            line.third_down_targets += third or 0
            line.red_zone_carries += rzc or 0
            line.goal_line_carries += glc or 0
        for gsis, played in games.items():
            lines[gsis].team_goal_line_carries = sum(team_gl[game] for game in played)

    def _add_snaps(self, lines: dict[str, SeasonLine]) -> None:
        rows = self.conn.execute(
            """SELECT p.gsis_id, s.offense_pct FROM snap_counts s JOIN players p ON p.pfr_id = s.pfr_id
               WHERE s.season = ? AND s.game_id LIKE ? AND s.offense_pct IS NOT NULL ORDER BY s.week""",
            (self.season, f"{self.season}_%"),
        ).fetchall()
        for gsis, pct in rows:
            line = lines.get(gsis)
            if line is not None:
                line.snaps.append(pct if pct <= 1 else pct / 100)

    def _rookies(self) -> set[str]:
        return {gsis for (gsis,) in self.conn.execute(
            "SELECT gsis_id FROM players WHERE rookie_season = ? OR (rookie_season IS NULL AND draft_year = ?)",
            (self.season, self.season))}

    def _trending(self) -> dict[int, float]:
        try:
            rows = self.conn.execute(
                """SELECT espn_id, percent_change FROM espn_player_market
                   WHERE snapshot_date = (SELECT max(snapshot_date) FROM espn_player_market)
                     AND percent_change >= ?""", (TRENDING_CHANGE,)).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {espn_id: change for espn_id, change in rows}


def player_stat_tags(position: Position, line: SeasonLine) -> list[PlayerTag]:
    """The stat-based tags a season line earns at a position."""
    tags: list[PlayerTag] = []
    add = lambda key, detail=None: tags.append(PlayerTag(key=key, detail=detail))  # noqa: E731
    games = line.games
    ppg = mean(line.points) if line.points else 0.0
    target_share = mean(line.target_share) if line.target_share else 0.0
    air_share = mean(line.air_yards_share) if line.air_yards_share else 0.0
    snap = mean(line.snaps) if line.snaps else None

    if position in (WR, TE) and line.pbp_targets >= MIN_TARGETS:
        middle = line.middle / line.pbp_targets
        adot = line.air_yards / line.pbp_targets
        deep = line.deep / line.pbp_targets
        behind = line.behind_los / line.pbp_targets
        depth = f"{adot:.1f}-yard average depth"
        if position == WR:
            if (middle >= 0.25 and adot <= 10.5) or (adot <= 8.5 and middle >= 0.18) or adot <= 6.5:
                add("slot", f"{_pct(middle)} of targets between the numbers, {depth}")
            elif middle <= 0.20 and adot >= 10:
                add("outside", f"{_pct(1 - middle)} of targets outside the numbers, {depth}")
        if adot >= 13.5 or deep >= 0.25:
            add("deep_threat", f"{depth}, {_pct(deep)} of targets 20+ yards")
        catch = line.receptions / line.targets if line.targets else 0
        chains = line.receiving_first_downs / line.receptions if line.receptions else 0
        if catch >= 0.70 and 7 <= adot <= 11 and chains >= 0.5:
            add("possession", f"{_pct(catch)} catch rate, {_pct(chains)} of catches move the chains")
        if behind >= 0.25:
            add("screen", f"{_pct(behind)} of targets at or behind the line")
    if position in (WR, TE, RB) and line.receptions >= 10:
        yac = line.yac / line.receptions
        if yac >= (8 if position == RB else 6):
            add("yac", f"{yac:.1f} yards after the catch per reception")
    if position in (WR, TE) and target_share >= 0.27:
        add("alpha", f"{_pct(target_share)} target share")
    if position == TE and target_share >= 0.17:
        add("receiving_te", f"{_pct(target_share)} target share")

    if position == RB:
        carry_share = line.carries / line.team_carries if line.team_carries else 0
        if carry_share >= 0.60 and (snap is None or snap >= 0.60):
            add("workhorse", f"{_pct(carry_share)} of team carries" + (f", {_pct(snap)} of snaps" if snap else ""))
        elif 0.30 <= carry_share <= 0.55:
            add("committee", f"{_pct(carry_share)} of team carries")
        if line.targets / games >= 4:
            add("pass_catching_back", f"{_per_game(line.targets / games)} targets a game")
    if position in (RB, QB) and line.goal_line_carries:
        gl_share = line.goal_line_carries / line.team_goal_line_carries if line.team_goal_line_carries else 0
        if line.goal_line_carries / games >= 0.8 or (gl_share >= 0.5 and line.goal_line_carries >= 5):
            add("goal_line_back", f"{line.goal_line_carries} carries inside the 5 ({_pct(gl_share)} of the team's)")

    if position == QB:
        if line.rushing_yards / games >= 30:
            add("rushing_qb", f"{line.rushing_yards / games:.0f} rushing yards a game")
        if line.attempts and line.passing_air_yards / line.attempts >= 8.5:
            add("gunslinger", f"{line.passing_air_yards / line.attempts:.1f}-yard average target depth")
        if line.attempts / games >= 36:
            add("high_volume_passer", f"{line.attempts / games:.0f} passes a game")

    if position in SKILL:
        if target_share >= (0.13 if position == RB else 0.22):
            add("high_target_share", f"{_pct(target_share)} of team targets")
        if position in (WR, TE) and air_share >= 0.33:
            add("air_yards_share", f"{_pct(air_share)} of team air yards")
        if line.red_zone_targets / games >= 1.2:
            add("red_zone_target", f"{_per_game(line.red_zone_targets / games)} red-zone targets a game")
        if line.end_zone_targets / games >= 0.8:
            add("end_zone_target", f"{line.end_zone_targets} end-zone targets")
        if line.third_down_targets / games >= 2.5:
            add("third_down", f"{_per_game(line.third_down_targets / games)} third-down targets a game")
    if position in (RB, QB) and line.red_zone_carries / games >= 2.5:
        add("red_zone_back", f"{_per_game(line.red_zone_carries / games)} red-zone carries a game")
    if position in SKILL and snap is not None and len(line.snaps) >= MIN_GAMES:
        if snap >= 0.80:
            add("full_time", f"{_pct(snap)} of snaps")
        elif 0.20 <= snap < 0.50:
            add("part_time", f"{_pct(snap)} of snaps")
        if len(line.snaps) > RECENT_GAMES + 1:
            recent, earlier = mean(line.snaps[-RECENT_GAMES:]), mean(line.snaps[:-RECENT_GAMES])
            if recent - earlier >= 0.15 and recent >= 0.5:
                add("snaps_rising", f"{_pct(earlier)} to {_pct(recent)} of snaps")

    if position in OFFENSE + [K] and games > RECENT_GAMES and ppg > 0:
        recent = mean(line.points[-RECENT_GAMES:])
        if recent >= ppg * 1.25 and recent >= 8:
            add("hot", f"{recent:.1f} points a game over the last {RECENT_GAMES}, {ppg:.1f} on the season")
        elif recent <= ppg * 0.70 and ppg >= 8:
            add("cold", f"{recent:.1f} points a game over the last {RECENT_GAMES}, {ppg:.1f} on the season")
    if position in OFFENSE and games >= 4 and ppg > 0:
        spread = pstdev(line.points) / ppg
        if ppg >= 8 and spread >= 0.65:
            add("boom_bust", f"{min(line.points):.1f} to {max(line.points):.1f} points")
        elif ppg >= 10 and spread <= 0.35:
            add("steady", f"{ppg:.1f} points a game, rarely below {min(line.points):.1f}")
    total = sum(line.points)
    if position in SKILL and total > 0 and ppg >= 6 and line.td_points / total >= 0.40:
        add("td_dependent", f"{_pct(line.td_points / total)} of points from touchdowns")
    return tags


def status_tags(player: Player, outlook: PlayerOutlook | None, current_week: int | None) -> list[PlayerTag]:
    """Tags that come from the player pool itself, not the season's stats."""
    tags = []
    status = (player.injury_status or "").upper()
    if status in INJURY_STATUSES and player.position != DST:
        tags.append(PlayerTag(key="injury", detail=INJURY_STATUSES[status]))
    if current_week and player.bye_week == current_week:
        tags.append(PlayerTag(key="on_bye", detail=f"Week {current_week}"))
    if outlook and outlook.sos_rank and outlook.sos_teams >= 10:
        if outlook.sos_rank <= 5:
            tags.append(PlayerTag(key="easy_schedule", detail=f"#{outlook.sos_rank} easiest of {outlook.sos_teams}"))
        elif outlook.sos_rank > outlook.sos_teams - 5:
            hardest = outlook.sos_teams - outlook.sos_rank + 1
            tags.append(PlayerTag(key="tough_schedule", detail=f"#{hardest} hardest of {outlook.sos_teams}"))
    return tags


def tag_index(players: list[Player], stat_tags: dict[str, list[PlayerTag]], outlooks: dict[str, PlayerOutlook],
              current_week: int | None, season: int | None) -> TagIndex:
    """Every player's tags (stat-based plus status), and the catalog with how many have each."""
    by_player: dict[str, list[PlayerTag]] = {}
    counts: dict[str, int] = defaultdict(int)
    for player in players:
        found = [t for t in stat_tags.get(player.id, []) if player.position in BY_KEY[t.key].positions]
        found += status_tags(player, outlooks.get(player.id), current_week)
        if not found:
            continue
        found.sort(key=lambda t: ORDER[t.key])
        by_player[player.id] = found
        for tag in found:
            counts[tag.key] += 1
    summaries = [TagSummary(**tag.model_dump(), count=counts[tag.key]) for tag in CATALOG]
    return TagIndex(season=season, tags=summaries, players=by_player)
