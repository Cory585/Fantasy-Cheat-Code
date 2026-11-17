"""Pull players and projections from Sleeper and ESPN into a local JSON cache.

Usage (from backend/):
    python -m app.data.sync                     # current season, current week -> week 18, PPR
    python -m app.data.sync --scoring half_ppr --start-week 10 --no-espn

The API reads only from this cache, so user requests never hit external providers.
All requests go through a rate-limited client (see sources/http.py).
"""

import argparse
import json
import logging
import os
import re
from collections.abc import Container
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from app.config import REGULAR_SEASON_END_WEEK, settings
from app.data.sources import espn, sleeper
from app.data.sources.espn import EspnSource, ProTeam
from app.data.sources.http import RateLimitedClient
from app.data.sources.sleeper import SleeperSource
from app.models.player import Player, Position
from app.models.trade import ScoringFormat

log = logging.getLogger(__name__)

# Sleeper injury_status -> short code used by ValuationService.INJURY_DISCOUNT.
INJURY_CODES = {
    "Questionable": "Q",
    "Doubtful": "D",
    "Out": "O",
    "Sus": "O",
    "COV": "O",
    "NA": "O",
    "DNR": "O",
    "IR": "IR",
    "PUP": "IR",
}


NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """'Kenneth Walker III' -> 'kenneth walker'; "Ja'Marr Chase" -> 'jamarr chase'."""
    cleaned = re.sub(r"[^a-z ]", "", name.lower().replace("-", " "))
    return " ".join(w for w in cleaned.split() if w not in NAME_SUFFIXES)


class EspnProjection(NamedTuple):
    espn_id: int
    name: str
    position: Position
    team: str | None
    points: float
    weekly: dict[int, float] | None = None  # points by week (None when not collected)


class EspnIndex:
    """Looks up an ESPN projection for a Sleeper player.

    Sleeper's `espn_id` is missing for most players, so this falls back to
    (name, position, team), then (name, position) when that is unambiguous.
    """

    def __init__(self, projections: list[EspnProjection]):
        self._by_id = {p.espn_id: p for p in projections}
        self._dst_by_team = {p.team: p for p in projections if p.position == Position.DST and p.team}
        self._by_name_pos_team = {(normalize_name(p.name), p.position, p.team): p for p in projections}
        by_name_pos: dict[tuple, list[EspnProjection]] = {}
        for p in projections:
            by_name_pos.setdefault((normalize_name(p.name), p.position), []).append(p)
        self._by_name_pos = {k: v[0] for k, v in by_name_pos.items() if len(v) == 1}

    def find(
        self,
        name: str,
        position: Position,
        team: str,
        espn_id: int | None,
        sleeper_keys: Container[tuple] = (),
    ) -> EspnProjection | None:
        """`sleeper_keys` holds every Sleeper (normalized name, position, team), so the
        name-only fallback never hands one player's projection to a namesake on another team."""
        if position == Position.DST:
            return self._dst_by_team.get(team)
        if espn_id and espn_id in self._by_id:
            return self._by_id[espn_id]
        key = normalize_name(name)
        exact = self._by_name_pos_team.get((key, position, team))
        if exact:
            return exact
        fallback = self._by_name_pos.get((key, position))
        if fallback and (key, position, fallback.team) in sleeper_keys:
            return None  # that ESPN entry belongs to the namesake on fallback.team
        return fallback


def _full_name(raw: dict) -> str:
    return raw.get("full_name") or f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()


def build_players(
    sleeper_players: dict[str, dict],
    sleeper_ros: dict[str, float],
    espn_index: EspnIndex,
    bye_weeks: dict[str, int | None],
    sleeper_weekly: dict[str, dict[int, float]] | None = None,
) -> list[Player]:
    """Merge provider data into Players. Skips anyone with no projection from any source.

    `sleeper_weekly` (Sleeper points by player and week) and each ESPN match's `weekly`
    become the player's `weekly_projections`."""
    sleeper_weekly = sleeper_weekly or {}
    relevant = {sid: raw for sid, raw in sleeper_players.items() if sleeper.is_fantasy_relevant(raw)}
    sleeper_keys = {
        (normalize_name(_full_name(raw)), sleeper.POSITION_MAP[raw["position"]], raw["team"])
        for raw in relevant.values()
    }
    players: list[Player] = []
    for sleeper_id, raw in relevant.items():
        position = sleeper.POSITION_MAP[raw["position"]]
        team = raw["team"]
        name = _full_name(raw)
        espn_id = int(raw["espn_id"]) if raw.get("espn_id") else None

        sources: dict[str, float] = {}
        weekly: dict[int, dict[str, float]] = {}
        if sleeper_ros.get(sleeper_id):
            sources["sleeper"] = round(sleeper_ros[sleeper_id], 2)
            _add_weekly(weekly, "sleeper", sleeper_weekly.get(sleeper_id))
        espn_match = espn_index.find(name, position, team, espn_id, sleeper_keys)
        if espn_match:
            espn_id = espn_id or espn_match.espn_id
            if espn_match.points:
                sources["espn"] = round(espn_match.points, 2)
                _add_weekly(weekly, "espn", espn_match.weekly)
        if not sources:
            continue

        status = raw.get("injury_status")
        players.append(
            Player(
                id=sleeper_id,
                name=name,
                position=position,
                team=team,
                bye_week=bye_weeks.get(team),
                ros_projection=round(sum(sources.values()) / len(sources), 2),
                injury_status=INJURY_CODES.get(status, status) if status else None,
                sleeper_id=sleeper_id,
                espn_id=espn_id,
                projection_sources=sources,
                weekly_projections=dict(sorted(weekly.items())),
            )
        )
    return sorted(players, key=lambda p: p.ros_projection, reverse=True)


def _add_weekly(weekly: dict[int, dict[str, float]], source: str, points: dict[int, float] | None) -> None:
    for week, value in (points or {}).items():
        if value:
            weekly.setdefault(week, {})[source] = round(value, 2)


def espn_projections(
    espn_players: list[dict],
    teams: dict[int, ProTeam],
    season: int,
    weeks: range,
    scoring: ScoringFormat,
    played_first_week: frozenset[str] = frozenset(),
) -> list[EspnProjection]:
    projections = []
    for p in espn_players:
        position = espn.POSITION_MAP.get(p.get("defaultPositionId"))
        if position is None:
            continue
        team = teams.get(p.get("proTeamId"))
        weekly = espn.weekly_points(
            p, season, weeks, scoring,
            skip_week=weeks.start if team and team.abbrev in played_first_week else None,
        )
        projections.append(
            EspnProjection(
                espn_id=p["id"],
                name=p.get("fullName", ""),
                position=position,
                team=team.abbrev if team else None,
                points=sum(weekly.values()),
                weekly=weekly,
            )
        )
    return projections


class SyncError(Exception):
    """The sync can't produce a usable cache; the existing cache is left in place."""


# A new cache with fewer than this share of the current cache's players is refused (unless
# forced), since a provider outage or a bad week range is far likelier than half the league
# vanishing.
MIN_CACHE_FRACTION = 0.5


def sync(
    output: Path,
    scoring: ScoringFormat,
    season: int | None = None,
    start_week: int | None = None,
    end_week: int = REGULAR_SEASON_END_WEEK,
    use_espn: bool = True,
    force: bool = False,
) -> list[Player]:
    """Raises SyncError, without touching `output`, when there is nothing worth writing."""
    with RateLimitedClient(min_interval=settings.request_interval_seconds) as client:
        sleeper_src = SleeperSource(client)
        state = sleeper_src.nfl_state()
        season = season or int(state["season"])
        start_week = start_week or max(int(state["week"]), 1)
        if start_week > end_week:
            raise SyncError(
                f"Start week {start_week} is past end week {end_week}, so there are no weeks to project. "
                "Pass --start-week and/or --end-week to pick a range."
            )
        weeks = range(start_week, end_week + 1)
        log.info("Syncing %s season, weeks %d-%d, %s scoring", season, start_week, end_week, scoring.value)

        # Games already played in the first week shouldn't count toward rest of season.
        played = espn.teams_with_completed_games(client, season, start_week)
        log.info("%d teams have already played week %d; skipping those games", len(played), start_week)

        log.info("Fetching Sleeper players...")
        sleeper_players = sleeper_src.players()
        log.info("Fetching Sleeper projections (%d weekly requests)...", len(weeks))
        sleeper_weekly = sleeper_src.weekly_points(season, weeks, scoring, played_first_week=played)
        sleeper_ros = sleeper.ros_totals(sleeper_weekly)

        espn_proj: list[EspnProjection] = []
        bye_weeks: dict[str, int | None] = {}
        if use_espn:
            espn_src = EspnSource(client)
            log.info("Fetching ESPN teams and player projections...")
            teams = espn_src.pro_teams(season)
            bye_weeks = {t.abbrev: t.bye_week for t in teams.values()}
            espn_proj = espn_projections(
                espn_src.players(season, scoring), teams, season, weeks, scoring, played_first_week=played
            )

    players = build_players(sleeper_players, sleeper_ros, EspnIndex(espn_proj), bye_weeks, sleeper_weekly)
    write_cache(output, players, season=season, weeks=weeks, scoring=scoring,
                season_start=season_start(state, season), force=force)
    log.info("Wrote %d players to %s", len(players), output)
    return players


def season_start(state: dict, season: int) -> str | None:
    """Sleeper's `season_start_date`, if its state is for `season` (it only describes the
    current season, so a sync of another season with --season doesn't get it)."""
    try:
        current = int(state.get("season"))
    except (TypeError, ValueError):
        return None
    return state.get("season_start_date") if current == season else None


def write_cache(
    output: Path, players: list[Player], season: int, weeks: range, scoring: ScoringFormat,
    season_start: str | None = None, force: bool = False,
) -> None:
    """`season_start` is Sleeper's `season_start_date`; without a collected schedule, the API
    counts the current week from it.

    Raises SyncError instead of writing an empty cache, or (unless `force`) one with under
    MIN_CACHE_FRACTION of the current cache's players, so the previous cache keeps serving."""
    if not players:
        raise SyncError(f"No players to write; keeping the existing cache at {output}.")
    current = _cached_player_count(output)
    if not force and current and len(players) < current * MIN_CACHE_FRACTION:
        raise SyncError(
            f"Only {len(players)} players, down from {current} in {output}; keeping the existing cache. "
            "Pass --force to write it anyway."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "weeks": [weeks.start, weeks.stop - 1],
        "scoring": scoring.value,
        **({"season_start": season_start} if season_start else {}),
        "players": [p.model_dump(mode="json") for p in players],
    }
    # Write to a temp file and swap it in, so the running API never reads a half-written cache.
    tmp = output.with_name(output.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, output)


def _cached_player_count(path: Path) -> int:
    """How many players the cache at `path` holds (0 if it's missing or unreadable)."""
    try:
        players = json.loads(path.read_text(encoding="utf-8")).get("players")
    except (OSError, ValueError, AttributeError):
        return 0
    return len(players) if isinstance(players, list) else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scoring", type=ScoringFormat, default=ScoringFormat(settings.default_scoring))
    parser.add_argument("--season", type=int)
    parser.add_argument("--start-week", type=int, help="Default: Sleeper's current week")
    parser.add_argument("--end-week", type=int, default=REGULAR_SEASON_END_WEEK)
    parser.add_argument("--no-espn", action="store_true", help="Use Sleeper projections only")
    parser.add_argument("--output", type=Path, default=settings.player_cache_path)
    parser.add_argument("--force", action="store_true",
                        help="Write the cache even if it has far fewer players than the current one")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        sync(args.output, args.scoring, args.season, args.start_week, args.end_week,
             use_espn=not args.no_espn, force=args.force)
    except SyncError as error:
        raise SystemExit(f"Sync failed: {error}") from None


if __name__ == "__main__":
    main()
