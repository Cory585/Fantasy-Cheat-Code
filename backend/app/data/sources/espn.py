"""ESPN Fantasy data source.

ESPN has no official public API; this uses the endpoints the ESPN Fantasy web
app calls. Player projections from `leaguedefaults` need no auth. Private league
data (rosters, settings) would need the `espn_s2` and `SWID` cookies; not used yet.

Responses are large (~25 MB for the full player pool), so this is sync-job only.
"""

import json
import logging
from typing import NamedTuple

from app.data.sources.http import RateLimitedClient
from app.models.player import Position
from app.models.trade import ScoringFormat

API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
# ESPN's public sports API (no auth): teams, rosters, schedules, injuries, news, game logs.
SITE_API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

POSITION_MAP = {
    1: Position.QB,
    2: Position.RB,
    3: Position.WR,
    4: Position.TE,
    5: Position.K,
    16: Position.DST,
}
# Lineup slot IDs for QB, RB, WR, TE, D/ST, K (used to filter the player pool).
SLOT_IDS = [0, 2, 4, 6, 16, 17]

# ESPN default-league scoring: 1 = standard, 3 = PPR. Half-PPR is derived from PPR.
LEAGUE_DEFAULTS = {ScoringFormat.STANDARD: 1, ScoringFormat.HALF_PPR: 3, ScoringFormat.PPR: 3}
STAT_RECEPTIONS = "53"
STAT_SOURCE_PROJECTED = 1
STAT_SPLIT_WEEKLY = 1

log = logging.getLogger(__name__)

# ESPN abbreviations that differ from Sleeper's.
TEAM_ALIASES = {"WSH": "WAS"}


class ProTeam(NamedTuple):
    abbrev: str
    bye_week: int | None


class EspnSource:
    def __init__(self, client: RateLimitedClient):
        self._client = client

    def pro_teams(self, season: int) -> dict[int, ProTeam]:
        """ESPN proTeamId -> team abbreviation (Sleeper style) and bye week."""
        data = self._client.get_json(f"{API}/seasons/{season}", params={"view": "proTeamSchedules_wl"})
        return {
            t["id"]: ProTeam(TEAM_ALIASES.get(t["abbrev"], t["abbrev"]), t.get("byeWeek"))
            for t in data["settings"]["proTeams"]
            if t["id"]
        }

    def players(self, season: int, scoring: ScoringFormat, limit: int = 1500) -> list[dict]:
        fantasy_filter = {
            "players": {
                "filterSlotIds": {"value": SLOT_IDS},
                "limit": limit,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            }
        }
        data = self._client.get_json(
            f"{API}/seasons/{season}/segments/0/leaguedefaults/{LEAGUE_DEFAULTS[scoring]}",
            params={"view": "kona_player_info"},
            headers={"X-Fantasy-Filter": json.dumps(fantasy_filter)},
        )
        return [entry["player"] for entry in data["players"]]


def weekly_points(
    player: dict, season: int, weeks: range, scoring: ScoringFormat, skip_week: int | None = None
) -> dict[int, float]:
    """A player's ESPN projection for each week in `weeks` (weeks without one are left out),
    optionally skipping one week (used when the player's game that week is already final)."""
    points_by_week: dict[int, float] = {}
    for stat in player.get("stats") or []:
        if (
            stat.get("statSourceId") == STAT_SOURCE_PROJECTED
            and stat.get("statSplitTypeId") == STAT_SPLIT_WEEKLY
            and stat.get("seasonId") == season
            and stat.get("scoringPeriodId") in weeks
            and stat.get("scoringPeriodId") != skip_week
        ):
            points = stat.get("appliedTotal") or 0.0
            if scoring == ScoringFormat.HALF_PPR:
                points -= 0.5 * (stat.get("stats") or {}).get(STAT_RECEPTIONS, 0.0)
            week = stat["scoringPeriodId"]
            points_by_week[week] = points_by_week.get(week, 0.0) + points
    return points_by_week


def teams_with_completed_games(client: RateLimitedClient, season: int, week: int) -> frozenset[str]:
    """Teams whose regular-season game in `week` is final, from ESPN's public scoreboard."""
    data = client.get_json(f"{SITE_API}/scoreboard", params={"seasontype": 2, "week": week, "dates": season})
    return parse_completed_teams(data)


def scoreboard_competition(event) -> dict | None:
    """An ESPN scoreboard event's game, or None (logged) if the event is malformed."""
    competitions = event.get("competitions") if isinstance(event, dict) else None
    competition = competitions[0] if isinstance(competitions, list) and competitions else None
    if not isinstance(competition, dict):
        log.warning("Skipping malformed ESPN scoreboard event: %r", event)
        return None
    return competition


def is_completed(competition: dict) -> bool:
    status = competition.get("status")
    status_type = status.get("type") if isinstance(status, dict) else None
    return isinstance(status_type, dict) and bool(status_type.get("completed"))


def competitor_abbrev(competitor) -> str | None:
    """A scoreboard competitor's team abbreviation (Sleeper style), or None if it's missing."""
    team = competitor.get("team") if isinstance(competitor, dict) else None
    abbrev = team.get("abbreviation") if isinstance(team, dict) else None
    return TEAM_ALIASES.get(abbrev, abbrev) if isinstance(abbrev, str) and abbrev else None


def parse_completed_teams(data: dict) -> frozenset[str]:
    teams = set()
    for event in data.get("events") or []:
        competition = scoreboard_competition(event)
        if competition is None or not is_completed(competition):
            continue
        for competitor in competition.get("competitors") or []:
            abbrev = competitor_abbrev(competitor)
            if abbrev:
                teams.add(abbrev)
    return frozenset(teams)


def fetch_site_teams(client: RateLimitedClient) -> list[dict]:
    """All 32 NFL teams from ESPN's public sports API, as rows for the `teams` table."""
    data = client.get_json(f"{SITE_API}/teams")
    return parse_site_teams(data)


def parse_site_teams(data: dict) -> list[dict]:
    rows = []
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        team = entry["team"]
        logos = team.get("logos") or []
        rows.append(
            {
                "abbrev": TEAM_ALIASES.get(team["abbreviation"], team["abbreviation"]),
                "espn_id": int(team["id"]),
                "location": team.get("location"),
                "name": team.get("name"),
                "display_name": team.get("displayName"),
                "color": team.get("color"),
                "logo_url": logos[0]["href"] if logos else None,
            }
        )
    return rows


def parse_market(players: list[dict], teams: dict[int, ProTeam], snapshot_date: str) -> list[dict]:
    """Rostered/started %, ADP, auction value, and draft ranks from the fantasy player feed.

    These fields are in the same player feed as the projections (`EspnSource.players`).
    """
    rows = []
    for p in players:
        position = POSITION_MAP.get(p.get("defaultPositionId"))
        if position is None:
            continue
        ownership = p.get("ownership") or {}
        ranks = p.get("draftRanksByRankType") or {}
        team = teams.get(p.get("proTeamId"))
        rows.append(
            {
                "espn_id": p["id"],
                "snapshot_date": snapshot_date,
                "name": p.get("fullName"),
                "position": position.value,
                "team": team.abbrev if team else None,
                "percent_owned": ownership.get("percentOwned"),
                "percent_started": ownership.get("percentStarted"),
                "percent_change": ownership.get("percentChange"),
                "adp": ownership.get("averageDraftPosition"),
                "auction_value": ownership.get("auctionValueAverage"),
                "rank_standard": (ranks.get("STANDARD") or {}).get("rank"),
                "rank_ppr": (ranks.get("PPR") or {}).get("rank"),
            }
        )
    return rows
