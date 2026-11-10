"""Sleeper data source (https://docs.sleeper.com).

Free, no auth. Sleeper asks that the full player dump (/players/nfl, ~15 MB) be
fetched at most once per day, so it is only pulled by the sync job.
The projections endpoint is undocumented and may change.
"""

from app.data.sources.http import RateLimitedClient
from app.models.player import Position
from app.models.trade import ScoringFormat

API = "https://api.sleeper.app/v1"
PROJECTIONS_API = "https://api.sleeper.app/projections/nfl"

# Sleeper calls team defenses "DEF"; their player_id is the team abbreviation.
POSITION_MAP = {
    "QB": Position.QB,
    "RB": Position.RB,
    "WR": Position.WR,
    "TE": Position.TE,
    "K": Position.K,
    "DEF": Position.DST,
}

POINTS_FIELD = {
    ScoringFormat.STANDARD: "pts_std",
    ScoringFormat.HALF_PPR: "pts_half_ppr",
    ScoringFormat.PPR: "pts_ppr",
}


class SleeperSource:
    def __init__(self, client: RateLimitedClient):
        self._client = client

    def nfl_state(self) -> dict:
        """Current season and week, e.g. {"season": "2026", "week": 9, ...}."""
        return self._client.get_json(f"{API}/state/nfl")

    def players(self) -> dict[str, dict]:
        """All NFL players keyed by Sleeper player_id."""
        return self._client.get_json(f"{API}/players/nfl")

    def weekly_projections(self, season: int, week: int) -> list[dict]:
        params = [("season_type", "regular")] + [("position[]", p) for p in POSITION_MAP]
        return self._client.get_json(f"{PROJECTIONS_API}/{season}/{week}", params=params)

    def ros_projections(
        self, season: int, weeks: range, scoring: ScoringFormat
    ) -> dict[str, float]:
        """Sum of weekly projected points per Sleeper player_id over `weeks`."""
        totals: dict[str, float] = {}
        for week in weeks:
            for player_id, points in parse_weekly_points(
                self.weekly_projections(season, week), scoring
            ).items():
                totals[player_id] = totals.get(player_id, 0.0) + points
        return totals


def parse_weekly_points(rows: list[dict], scoring: ScoringFormat) -> dict[str, float]:
    field = POINTS_FIELD[scoring]
    points: dict[str, float] = {}
    for row in rows:
        player_id = row.get("player_id")
        value = (row.get("stats") or {}).get(field)
        if player_id and value:
            points[player_id] = float(value)
    return points


def is_fantasy_relevant(raw: dict) -> bool:
    return bool(raw.get("team")) and raw.get("position") in POSITION_MAP and raw.get("active", True)
