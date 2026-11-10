"""Collect player and team data from public sources into the SQLite database.

Usage (from backend/):
    python -m app.data.collect                          # every dataset, current season
    python -m app.data.collect --season 2025
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

A full run is about 9 requests. All of them go through the rate-limited client.
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
from app.models.trade import ScoringFormat

log = logging.getLogger(__name__)

ESPN_DATASETS = ("teams", "espn_market")
ALL_DATASETS = ESPN_DATASETS + tuple(NFLVERSE_DATASETS)


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
    raise ValueError(f"Unknown dataset '{dataset}'. Choose from: {', '.join(ALL_DATASETS)}")


def collect(datasets: list[str], season: int, conn) -> dict[str, int | Exception]:
    """Collect each dataset into the database. One failure doesn't stop the others."""
    results: dict[str, int | Exception] = {}
    with RateLimitedClient(min_interval=settings.request_interval_seconds) as client:
        for dataset in datasets:
            log.info("Collecting %s (%s season)...", dataset, season)
            try:
                table, rows = collect_dataset(client, dataset, season)
                count = db.upsert(conn, table, rows)
                db.record_run(conn, dataset, season, count)
                results[dataset] = count
                log.info("  %s: %d rows", dataset, count)
            except Exception as error:  # noqa: BLE001 - report and continue with the rest
                log.error("  %s failed: %s", dataset, error)
                results[dataset] = error
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=current_season())
    parser.add_argument("--datasets", default=",".join(ALL_DATASETS), help="Comma-separated list")
    parser.add_argument("--db", type=Path, default=settings.database_path)
    args = parser.parse_args()

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = [d for d in datasets if d not in ALL_DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Choose from: {', '.join(ALL_DATASETS)}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = db.connect(args.db)
    try:
        results = collect(datasets, args.season, conn)
    finally:
        conn.close()

    failed = [d for d, r in results.items() if isinstance(r, Exception)]
    log.info("Done: %d datasets collected into %s, %d failed", len(results) - len(failed), args.db, len(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
