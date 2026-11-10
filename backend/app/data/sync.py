"""Pull players and projections from Sleeper and ESPN into a local JSON cache.

Usage (from backend/):
    python -m app.data.sync                     # current season, current week -> week 17, PPR
    python -m app.data.sync --scoring half_ppr --start-week 10 --no-espn

The API reads only from this cache, so user requests never hit external providers.
All requests go through a rate-limited client (see sources/http.py).
"""

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from app.config import settings
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

    def find(self, name: str, position: Position, team: str, espn_id: int | None) -> EspnProjection | None:
        if position == Position.DST:
            return self._dst_by_team.get(team)
        if espn_id and espn_id in self._by_id:
            return self._by_id[espn_id]
        key = normalize_name(name)
        return self._by_name_pos_team.get((key, position, team)) or self._by_name_pos.get((key, position))


def build_players(
    sleeper_players: dict[str, dict],
    sleeper_ros: dict[str, float],
    espn_index: EspnIndex,
    bye_weeks: dict[str, int | None],
) -> list[Player]:
    """Merge provider data into Players. Skips anyone with no projection from any source."""
    players: list[Player] = []
    for sleeper_id, raw in sleeper_players.items():
        if not sleeper.is_fantasy_relevant(raw):
            continue
        position = sleeper.POSITION_MAP[raw["position"]]
        team = raw["team"]
        name = raw.get("full_name") or f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()
        espn_id = int(raw["espn_id"]) if raw.get("espn_id") else None

        sources: dict[str, float] = {}
        if sleeper_ros.get(sleeper_id):
            sources["sleeper"] = round(sleeper_ros[sleeper_id], 2)
        espn_match = espn_index.find(name, position, team, espn_id)
        if espn_match:
            espn_id = espn_id or espn_match.espn_id
            if espn_match.points:
                sources["espn"] = round(espn_match.points, 2)
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
            )
        )
    return sorted(players, key=lambda p: p.ros_projection, reverse=True)


def espn_projections(
    espn_players: list[dict],
    teams: dict[int, ProTeam],
    season: int,
    weeks: range,
    scoring: ScoringFormat,
) -> list[EspnProjection]:
    projections = []
    for p in espn_players:
        position = espn.POSITION_MAP.get(p.get("defaultPositionId"))
        if position is None:
            continue
        team = teams.get(p.get("proTeamId"))
        projections.append(
            EspnProjection(
                espn_id=p["id"],
                name=p.get("fullName", ""),
                position=position,
                team=team.abbrev if team else None,
                points=espn.ros_points(p, season, weeks, scoring),
            )
        )
    return projections


def sync(
    output: Path,
    scoring: ScoringFormat,
    season: int | None = None,
    start_week: int | None = None,
    end_week: int = 17,
    use_espn: bool = True,
) -> list[Player]:
    with RateLimitedClient(min_interval=settings.request_interval_seconds) as client:
        sleeper_src = SleeperSource(client)
        state = sleeper_src.nfl_state()
        season = season or int(state["season"])
        start_week = start_week or max(int(state["week"]), 1)
        weeks = range(start_week, end_week + 1)
        log.info("Syncing %s season, weeks %d-%d, %s scoring", season, start_week, end_week, scoring.value)

        log.info("Fetching Sleeper players...")
        sleeper_players = sleeper_src.players()
        log.info("Fetching Sleeper projections (%d weekly requests)...", len(weeks))
        sleeper_ros = sleeper_src.ros_projections(season, weeks, scoring)

        espn_proj: list[EspnProjection] = []
        bye_weeks: dict[str, int | None] = {}
        if use_espn:
            espn_src = EspnSource(client)
            log.info("Fetching ESPN teams and player projections...")
            teams = espn_src.pro_teams(season)
            bye_weeks = {t.abbrev: t.bye_week for t in teams.values()}
            espn_proj = espn_projections(espn_src.players(season, scoring), teams, season, weeks, scoring)

    players = build_players(sleeper_players, sleeper_ros, EspnIndex(espn_proj), bye_weeks)
    write_cache(output, players, season=season, weeks=weeks, scoring=scoring)
    log.info("Wrote %d players to %s", len(players), output)
    return players


def write_cache(
    output: Path, players: list[Player], season: int, weeks: range, scoring: ScoringFormat
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "weeks": [weeks.start, weeks.stop - 1],
        "scoring": scoring.value,
        "players": [p.model_dump(mode="json") for p in players],
    }
    output.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scoring", type=ScoringFormat, default=ScoringFormat(settings.default_scoring))
    parser.add_argument("--season", type=int)
    parser.add_argument("--start-week", type=int, help="Default: Sleeper's current week")
    parser.add_argument("--end-week", type=int, default=17)
    parser.add_argument("--no-espn", action="store_true", help="Use Sleeper projections only")
    parser.add_argument("--output", type=Path, default=settings.player_cache_path)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sync(args.output, args.scoring, args.season, args.start_week, args.end_week, use_espn=not args.no_espn)


if __name__ == "__main__":
    main()
