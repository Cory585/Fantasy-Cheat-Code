"""Collect player and team data from public sources into the SQLite database.

Usage (from backend/):
    python -m app.data.collect                          # every dataset, current season
    python -m app.data.collect --seasons 2022-2026      # history (e.g. for model training)
    python -m app.data.collect --datasets player_week_stats,injuries

Datasets:
    teams              ESPN public sports API: names, colors, logos
    espn_market        ESPN Fantasy: rostered/started %, ADP, auction value, draft ranks (daily snapshot)
    players            nflverse: every player with GSIS/ESPN/PFR/PFF IDs, bio, draft info
    games              nflverse: schedule and results since 1999, Vegas spread and total
    player_week_stats  nflverse: ~150 stats per player per week, including fantasy points
    team_week_stats    nflverse: team offense/defense/special teams per week
    snap_counts        nflverse: offense/defense/special teams snaps and share
    injuries           nflverse: weekly injury report and practice participation
    combine            nflverse: NFL Scouting Combine results since 2000 (40, bench, jumps, agility)
    playcalling        nflverse play-by-play, summarized per offense per game: neutral pass rate
                       over expected (coaching tendency), pace, sacks, turnovers, EPA per dropback

teams, espn_market, players, and games cover every season, so they're only collected once
per run. A one-season run is about 10 requests. All of them go through the rate-limited client.
This complements `app.data.sync`, which builds the projections the API uses today.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from app.config import settings
from app.data import db
from app.data.sources import espn
from app.data.sources.espn import EspnSource
from app.data.sources.http import RateLimitedClient
from app.data.sources.nflverse import PARSERS as NFLVERSE_DATASETS
from app.data.sources.nflverse import NflverseSource
from app.data.sources.playcalling import fetch_team_game_offense
from app.models.trade import ScoringFormat

log = logging.getLogger(__name__)

ESPN_DATASETS = ("teams", "espn_market")
ALL_DATASETS = ESPN_DATASETS + tuple(NFLVERSE_DATASETS) + ("playcalling",)
# Datasets that aren't per season (or only make sense for the current one).
SEASONLESS_DATASETS = {"teams", "espn_market", "players", "games", "combine"}


def current_season(today: date | None = None) -> int:
    """NFL seasons start in September and end in February of the next year."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def collect_dataset(client: RateLimitedClient, dataset: str, season: int) -> tuple[str, list[dict]]:
    """Fetch one dataset. Returns (table name, rows)."""
    if dataset == "teams":
        return "teams", espn.fetch_site_teams(client)
    if dataset == "espn_market":
        source = EspnSource(client)
        teams = source.pro_teams(season)
        players = source.players(season, ScoringFormat.PPR)
        return "espn_player_market", espn.parse_market(players, teams, date.today().isoformat())
    if dataset in NFLVERSE_DATASETS:
        return dataset, NflverseSource(client).fetch(dataset, season)
    if dataset == "playcalling":
        return "team_game_offense", fetch_team_game_offense(client, season)
    raise ValueError(f"Unknown dataset '{dataset}'. Choose from: {', '.join(ALL_DATASETS)}")


def collect(datasets: list[str], seasons: list[int], conn) -> dict[str, int | Exception]:
    """Collect each dataset into the database. One failure doesn't stop the others.

    Results are keyed "dataset:season".
    """
    results: dict[str, int | Exception] = {}
    latest = max(seasons)
    with RateLimitedClient(min_interval=settings.request_interval_seconds) as client:
        for dataset in datasets:
            for season in [latest] if dataset in SEASONLESS_DATASETS else sorted(seasons):
                key = f"{dataset}:{season}"
                log.info("Collecting %s (%s season)...", dataset, season)
                try:
                    table, rows = collect_dataset(client, dataset, season)
                    count = db.upsert(conn, table, rows)
                    db.record_run(conn, dataset, season, count)
                    results[key] = count
                    log.info("  %s: %d rows", key, count)
                except Exception as error:  # noqa: BLE001 - report and continue with the rest
                    log.error("  %s failed: %s", key, error)
                    results[key] = error
    return results


def parse_seasons(text: str) -> list[int]:
    """'2026' -> [2026]; '2022-2026' -> [2022, ..., 2026]; '2023,2025' -> [2023, 2025]."""
    seasons: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            seasons.update(range(min(start, end), max(start, end) + 1))
        elif part:
            seasons.add(int(part))
    if not seasons:
        raise ValueError("no seasons given")
    return sorted(seasons)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", "--season", default=str(current_season()),
                        help="One season, a range (2022-2026), or a comma-separated list")
    parser.add_argument("--datasets", default=",".join(ALL_DATASETS), help="Comma-separated list")
    parser.add_argument("--db", type=Path, default=settings.database_path)
    args = parser.parse_args()

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    try:
        seasons = parse_seasons(args.seasons)
    except ValueError:
        parser.error(f"couldn't read seasons '{args.seasons}'")
    unknown = [d for d in datasets if d not in ALL_DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Choose from: {', '.join(ALL_DATASETS)}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = db.connect(args.db)
    try:
        results = collect(datasets, seasons, conn)
    finally:
        conn.close()

    failed = [d for d, r in results.items() if isinstance(r, Exception)]
    log.info("Done: %d collected into %s, %d failed", len(results) - len(failed), args.db, len(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
