"""Play-calling tendencies from nflverse play-by-play.

The full play-by-play file is ~19 MB gzipped per season with 372 columns. Rather than
store every play, this streams it once and keeps one row per offense per game with the
counts needed for tendency and efficiency metrics:

- Neutral-situation pass rate over expected (PROE): `pass_oe` averaged over plays with
  win probability between 20% and 80%, downs 1-3, more than 2 minutes left in the half.
  This is the standard way to measure how pass-happy a coaching staff is, independent of
  game script.
- Plays per game (pace), dropbacks, sacks taken, interceptions, fumbles lost, and EPA per
  dropback (QB/passing efficiency).

The same pass also keeps one row per player per game (`player_game_usage`) with where and
how they were used: targets by field location (left/middle/right, which is how the player
tags tell slot receivers from outside ones, since public data has no alignment), deep and
behind-the-line targets, red-zone and end-zone targets, third-down targets, and red-zone
and goal-line carries.
"""

import csv
import gzip
import io

from app.data.sources.http import RateLimitedClient
from app.data.sources.nflverse import RELEASES, TEAM_ALIASES

NEUTRAL_WP = (0.2, 0.8)
NEUTRAL_MIN_HALF_SECONDS = 120


def pbp_url(season: int) -> str:
    return f"{RELEASES}/pbp/play_by_play_{season}.csv.gz"


DEEP_AIR_YARDS = 20
RED_ZONE = 20  # yards from the end zone
GOAL_LINE = 5


def fetch_team_game_offense(client: RateLimitedClient, season: int) -> dict[str, list[dict]]:
    """Both summaries from one download: {"team_game_offense": [...], "player_game_usage": [...]}."""
    teams, usage = parse_play_by_play_tables(gzip.decompress(client.get_bytes(pbp_url(season))).decode("utf-8"))
    return {"team_game_offense": teams, "player_game_usage": usage}


def _f(value: str | None) -> float | None:
    if value in (None, "", "NA"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _team(value: str) -> str:
    return TEAM_ALIASES.get(value, value)


def parse_play_by_play(text: str) -> list[dict]:
    """One row per (game, offense) with summed play-calling and efficiency counts."""
    return parse_play_by_play_tables(text)[0]


USAGE_COUNTS = (
    "targets", "targets_left", "targets_middle", "targets_right", "air_yards", "deep_targets",
    "behind_los_targets", "red_zone_targets", "end_zone_targets", "third_down_targets",
    "carries", "red_zone_carries", "goal_line_carries",
)


def _usage_row(usage: dict, play: dict, player: str, team: str) -> dict:
    key = (play["game_id"], player)
    row = usage.get(key)
    if row is None:
        row = usage[key] = {"game_id": play["game_id"], "gsis_id": player, "season": int(play["season"]),
                            "week": int(play["week"]), "season_type": play.get("season_type") or "REG",
                            "team": team, **{name: 0 for name in USAGE_COUNTS}}
    return row


def _count_usage(usage: dict, play: dict, team: str) -> None:
    if play.get("two_point_attempt") == "1" or play.get("sack") == "1":
        return
    yardline, down = _f(play.get("yardline_100")), _f(play.get("down"))
    red_zone = yardline is not None and yardline <= RED_ZONE
    receiver = play.get("receiver_player_id")
    if play.get("pass_attempt") == "1" and receiver and receiver != "NA":
        row = _usage_row(usage, play, receiver, team)
        row["targets"] += 1
        location = play.get("pass_location")
        if location in ("left", "middle", "right"):
            row[f"targets_{location}"] += 1
        air = _f(play.get("air_yards"))
        if air is not None:
            row["air_yards"] += air
            row["deep_targets"] += air >= DEEP_AIR_YARDS
            row["behind_los_targets"] += air <= 0
            row["end_zone_targets"] += yardline is not None and air >= yardline
        row["red_zone_targets"] += red_zone
        row["third_down_targets"] += down is not None and down >= 3
    rusher = play.get("rusher_player_id")
    if play.get("rush_attempt") == "1" and rusher and rusher != "NA":
        row = _usage_row(usage, play, rusher, team)
        row["carries"] += 1
        row["red_zone_carries"] += red_zone
        row["goal_line_carries"] += yardline is not None and yardline <= GOAL_LINE


def parse_play_by_play_tables(text: str) -> tuple[list[dict], list[dict]]:
    """(one row per game and offense, one row per game and player who was targeted or ran)."""
    games: dict[tuple[str, str], dict] = {}
    usage: dict[tuple[str, str], dict] = {}
    for play in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        offense, defense = play.get("posteam"), play.get("defteam")
        if offense and play.get("play_type") != "no_play":
            _count_usage(usage, play, _team(offense))
        dropback = play.get("qb_dropback") == "1"
        rush = play.get("rush") == "1"
        if not offense or not defense or not (dropback or rush) or play.get("play_type") == "no_play":
            continue
        key = (play["game_id"], offense)
        row = games.get(key)
        if row is None:
            row = games[key] = {
                "game_id": play["game_id"],
                "season": int(play["season"]),
                "week": int(play["week"]),
                "season_type": play.get("season_type") or "REG",
                "team": _team(offense),
                "opponent": _team(defense),
                "plays": 0, "dropbacks": 0, "sacks": 0, "interceptions": 0, "fumbles_lost": 0,
                "dropback_epa": 0.0, "neutral_plays": 0, "neutral_dropbacks": 0,
                "pass_oe_sum": 0.0, "pass_oe_n": 0,
            }
        row["plays"] += 1
        row["fumbles_lost"] += play.get("fumble_lost") == "1"
        if dropback:
            row["dropbacks"] += 1
            row["sacks"] += play.get("sack") == "1"
            row["interceptions"] += play.get("interception") == "1"
            row["dropback_epa"] += _f(play.get("qb_epa")) or 0.0

        wp, down, half_secs = _f(play.get("wp")), _f(play.get("down")), _f(play.get("half_seconds_remaining"))
        neutral = (
            wp is not None and NEUTRAL_WP[0] <= wp <= NEUTRAL_WP[1]
            and down is not None and down <= 3
            and half_secs is not None and half_secs > NEUTRAL_MIN_HALF_SECONDS
        )
        if neutral:
            row["neutral_plays"] += 1
            row["neutral_dropbacks"] += dropback
            pass_oe = _f(play.get("pass_oe"))
            if pass_oe is not None:
                row["pass_oe_sum"] += pass_oe
                row["pass_oe_n"] += 1
    for row in games.values():
        row["dropback_epa"] = round(row["dropback_epa"], 4)
        row["pass_oe_sum"] = round(row["pass_oe_sum"], 4)
    for row in usage.values():
        row["air_yards"] = round(row["air_yards"], 1)
    return list(games.values()), list(usage.values())
