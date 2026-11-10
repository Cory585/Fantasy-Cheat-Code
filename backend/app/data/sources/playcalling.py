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


def fetch_team_game_offense(client: RateLimitedClient, season: int) -> list[dict]:
    return parse_play_by_play(gzip.decompress(client.get_bytes(pbp_url(season))).decode("utf-8"))


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
    games: dict[tuple[str, str], dict] = {}
    for play in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        offense, defense = play.get("posteam"), play.get("defteam")
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
    return list(games.values())
