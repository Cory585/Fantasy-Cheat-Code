"""nflverse data source (https://github.com/nflverse/nflverse-data).

Free, public CSV files published as GitHub release assets and refreshed during the
season (weekly stats usually by the morning after games). No auth. Each file is one
request; the largest (players.csv) is ~7 MB.

The `parse_*` functions turn CSV text into rows for the tables in `app.data.db`.
"""

import csv
import io
import json
from collections.abc import Callable

from app.data.sources.http import RateLimitedClient

RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"

# nflverse abbreviations that differ from the ones used elsewhere in the app (Sleeper's).
TEAM_ALIASES = {"LA": "LAR"}


def dataset_urls(season: int) -> dict[str, str]:
    return {
        "players": f"{RELEASES}/players/players.csv",
        "games": f"{RELEASES}/schedules/games.csv",  # every season since 1999
        "player_week_stats": f"{RELEASES}/stats_player/stats_player_week_{season}.csv",
        "team_week_stats": f"{RELEASES}/stats_team/stats_team_week_{season}.csv",
        "snap_counts": f"{RELEASES}/snap_counts/snap_counts_{season}.csv",
        "injuries": f"{RELEASES}/injuries/injuries_{season}.csv",
        "combine": f"{RELEASES}/combine/combine.csv",  # every combine since 2000
    }


class NflverseSource:
    def __init__(self, client: RateLimitedClient):
        self._client = client

    def fetch(self, dataset: str, season: int) -> list[dict]:
        text = self._client.get_text(dataset_urls(season)[dataset])
        return PARSERS[dataset](text)


# ---------- value helpers ----------


def _num(value: str | None) -> float | int | None:
    if value is None or value == "" or value == "NA":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() and "." not in value else number


def _int(value: str | None) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _str(value: str | None) -> str | None:
    return value if value not in (None, "", "NA") else None


def _team(value: str | None) -> str | None:
    value = _str(value)
    return TEAM_ALIASES.get(value, value) if value else None


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def _stats_json(row: dict[str, str], exclude: set[str]) -> str:
    """Every non-empty column not promoted to its own column, numbers parsed."""
    stats = {}
    for key, value in row.items():
        if key in exclude or value in ("", "NA", None):
            continue
        number = _num(value)
        stats[key] = number if number is not None else value
    return json.dumps(stats, separators=(",", ":"))


# ---------- parsers ----------


def parse_players(text: str) -> list[dict]:
    return [
        {
            "gsis_id": r["gsis_id"],
            "name": r.get("display_name") or f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
            "position": _str(r.get("position")),
            "team": _team(r.get("latest_team")),
            "status": _str(r.get("status")),
            "espn_id": _int(r.get("espn_id")),
            "pfr_id": _str(r.get("pfr_id")),
            "pff_id": _str(r.get("pff_id")),
            "birth_date": _str(r.get("birth_date")),
            "height": _num(r.get("height")),
            "weight": _num(r.get("weight")),
            "years_of_experience": _int(r.get("years_of_experience")),
            "rookie_season": _int(r.get("rookie_season")),
            "draft_year": _int(r.get("draft_year")),
            "draft_round": _int(r.get("draft_round")),
            "draft_pick": _int(r.get("draft_pick")),
            "headshot_url": _str(r.get("headshot")),
        }
        for r in _rows(text)
        if r.get("gsis_id")
    ]


def parse_games(text: str) -> list[dict]:
    return [
        {
            "game_id": r["game_id"],
            "season": _int(r["season"]),
            "game_type": _str(r.get("game_type")),
            "week": _int(r.get("week")),
            "gameday": _str(r.get("gameday")),
            "gametime": _str(r.get("gametime")),
            "away_team": _team(r.get("away_team")),
            "home_team": _team(r.get("home_team")),
            "away_score": _int(r.get("away_score")),
            "home_score": _int(r.get("home_score")),
            "spread_line": _num(r.get("spread_line")),
            "total_line": _num(r.get("total_line")),
            "roof": _str(r.get("roof")),
            "surface": _str(r.get("surface")),
            "temp": _num(r.get("temp")),
            "wind": _num(r.get("wind")),
            "div_game": _int(r.get("div_game")),
            "espn_id": _str(r.get("espn")),
        }
        for r in _rows(text)
        if r.get("game_id")
    ]


PLAYER_STAT_PROMOTED = {
    "player_id", "player_name", "player_display_name", "position", "position_group",
    "headshot_url", "season", "week", "season_type", "team", "opponent_team",
    "fantasy_points", "fantasy_points_ppr",
}


def parse_player_week_stats(text: str) -> list[dict]:
    return [
        {
            "gsis_id": r["player_id"],
            "season": _int(r["season"]),
            "week": _int(r["week"]),
            "season_type": r.get("season_type") or "REG",
            "name": _str(r.get("player_display_name")) or _str(r.get("player_name")),
            "position": _str(r.get("position")),
            "team": _team(r.get("team")),
            "opponent": _team(r.get("opponent_team")),
            "fantasy_points": _num(r.get("fantasy_points")),
            "fantasy_points_ppr": _num(r.get("fantasy_points_ppr")),
            "stats": _stats_json(r, PLAYER_STAT_PROMOTED),
        }
        for r in _rows(text)
        if r.get("player_id")
    ]


TEAM_STAT_PROMOTED = {"team", "season", "week", "season_type", "opponent_team"}


def parse_team_week_stats(text: str) -> list[dict]:
    return [
        {
            "team": _team(r["team"]),
            "season": _int(r["season"]),
            "week": _int(r["week"]),
            "season_type": r.get("season_type") or "REG",
            "opponent": _team(r.get("opponent_team")),
            "stats": _stats_json(r, TEAM_STAT_PROMOTED),
        }
        for r in _rows(text)
        if r.get("team")
    ]


def parse_snap_counts(text: str) -> list[dict]:
    return [
        {
            "pfr_id": r["pfr_player_id"],
            "game_id": r["game_id"],
            "season": _int(r["season"]),
            "week": _int(r.get("week")),
            "name": _str(r.get("player")),
            "position": _str(r.get("position")),
            "team": _team(r.get("team")),
            "opponent": _team(r.get("opponent")),
            "offense_snaps": _num(r.get("offense_snaps")),
            "offense_pct": _num(r.get("offense_pct")),
            "defense_snaps": _num(r.get("defense_snaps")),
            "defense_pct": _num(r.get("defense_pct")),
            "st_snaps": _num(r.get("st_snaps")),
            "st_pct": _num(r.get("st_pct")),
        }
        for r in _rows(text)
        if r.get("pfr_player_id") and r.get("game_id")
    ]


def parse_injuries(text: str) -> list[dict]:
    return [
        {
            "gsis_id": r["gsis_id"],
            "season": _int(r["season"]),
            "week": _int(r["week"]),
            "season_type": r.get("season_type") or "REG",
            "team": _team(r["team"]),
            "name": _str(r.get("full_name")),
            "position": _str(r.get("position")),
            "report_status": _str(r.get("report_status")),
            "practice_status": _str(r.get("practice_status")),
            "primary_injury": _str(r.get("report_primary_injury")) or _str(r.get("practice_primary_injury")),
        }
        for r in _rows(text)
        if r.get("gsis_id") and r.get("team") and r.get("week")
    ]


def _height_inches(value: str | None) -> float | None:
    """nflverse combine heights are feet-inches, e.g. "6-1"."""
    value = _str(value)
    if not value:
        return None
    feet, _, inches = value.partition("-")
    try:
        return int(feet) * 12 + float(inches or 0)
    except ValueError:
        return None


def parse_combine(text: str) -> list[dict]:
    rows = []
    for r in _rows(text):
        name, season = _str(r.get("player_name")), _int(r.get("season"))
        if not name or season is None:
            continue
        pfr_id = _str(r.get("pfr_id"))
        position, school = _str(r.get("pos")), _str(r.get("school"))
        rows.append({
            "combine_id": pfr_id or f"{season}:{name}:{position}:{school}",
            "season": season,
            "pfr_id": pfr_id,
            "cfb_id": _str(r.get("cfb_id")),
            "name": name,
            "position": position,
            "school": school,
            "draft_year": _int(r.get("draft_year")),
            "draft_team": _str(r.get("draft_team")),
            "draft_round": _int(r.get("draft_round")),
            "draft_pick": _int(r.get("draft_ovr")),
            "height_in": _height_inches(r.get("ht")),
            "weight_lb": _num(r.get("wt")),
            "forty": _num(r.get("forty")),
            "bench": _int(r.get("bench")),
            "vertical": _num(r.get("vertical")),
            "broad_jump": _int(r.get("broad_jump")),
            "cone": _num(r.get("cone")),
            "shuttle": _num(r.get("shuttle")),
        })
    return rows


PARSERS: dict[str, Callable[[str], list[dict]]] = {
    "players": parse_players,
    "games": parse_games,
    "player_week_stats": parse_player_week_stats,
    "team_week_stats": parse_team_week_stats,
    "snap_counts": parse_snap_counts,
    "injuries": parse_injuries,
    "combine": parse_combine,
}
