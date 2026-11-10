"""ESPN Fantasy data source.

ESPN has no official public API; this uses the endpoints the ESPN Fantasy web
app calls. Player projections from `leaguedefaults` need no auth. Private league
data (rosters, settings) would need the `espn_s2` and `SWID` cookies; not used yet.

Responses are large (~25 MB for the full player pool), so this is sync-job only.
"""

import json
from typing import NamedTuple

from app.data.sources.http import RateLimitedClient
from app.models.player import Position
from app.models.trade import ScoringFormat

API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

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


def ros_points(player: dict, season: int, weeks: range, scoring: ScoringFormat) -> float:
    """Sum of a player's weekly ESPN projections over `weeks`."""
    total = 0.0
    for stat in player.get("stats") or []:
        if (
            stat.get("statSourceId") == STAT_SOURCE_PROJECTED
            and stat.get("statSplitTypeId") == STAT_SPLIT_WEEKLY
            and stat.get("seasonId") == season
            and stat.get("scoringPeriodId") in weeks
        ):
            points = stat.get("appliedTotal") or 0.0
            if scoring == ScoringFormat.HALF_PPR:
                points -= 0.5 * (stat.get("stats") or {}).get(STAT_RECEPTIONS, 0.0)
            total += points
    return total
