"""Keep the synced and collected data fresh.

The sync (player pool, projections, injury report) and the collect (stats, schedules, ADP,
weather) are the only jobs that read outside providers. Run by hand, the data falls a week
behind by the next game day: rest-of-season totals and the injury report go stale on every page.

Once, from `backend/` (e.g. from cron or Task Scheduler):

    python -m app.data.refresh

Or while the API runs: set AUTO_REFRESH_HOURS (e.g. 6) and a background thread runs both jobs
that often, starting right away when the player cache is already older than that. Requests keep
reading the current files while a refresh runs; both jobs swap their results in when done, and
the API reloads files that changed.
"""

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.data import db
from app.data.collect import ALL_DATASETS, collect, current_season
from app.data.sync import SyncError, sync
from app.models.trade import ScoringFormat

log = logging.getLogger(__name__)


def refresh_once() -> bool:
    """Sync, then collect the current season. True if both worked; a failure is logged, and the
    files it would have replaced keep serving."""
    ok = True
    try:
        sync(settings.player_cache_path, ScoringFormat(settings.default_scoring))
    except SyncError as error:
        log.error("Refresh: sync failed: %s", error)
        ok = False
    except Exception:  # noqa: BLE001 - a network or provider error mustn't kill the refresher
        log.exception("Refresh: sync failed")
        ok = False
    try:
        conn = db.connect(settings.database_path)
        try:
            results = collect(list(ALL_DATASETS), [current_season()], conn)
        finally:
            conn.close()
        failed = [key for key, result in results.items() if isinstance(result, Exception)]
        if failed:
            log.error("Refresh: collect failed for %s", ", ".join(failed))
            ok = False
    except Exception:  # noqa: BLE001
        log.exception("Refresh: collect failed")
        ok = False
    return ok


def cache_age_hours(path: Path, now: datetime | None = None) -> float | None:
    """Hours since the player cache at `path` was synced, or None if it's missing or unreadable."""
    try:
        synced_at = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["synced_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - synced_at).total_seconds() / 3600


class Refresher:
    """Runs `job` every `hours` on a daemon thread. The first run is right away when the cache
    is missing or already older than `hours`, otherwise when it comes due."""

    def __init__(self, hours: float, job: Callable[[], bool] = refresh_once,
                 cache_path: Path | None = None):
        if hours <= 0:
            raise ValueError("hours must be positive")
        self.hours = hours
        self.job = job
        self.cache_path = cache_path or settings.player_cache_path
        self.runs = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def first_delay(self) -> float:
        """Seconds until the first refresh."""
        age = cache_age_hours(self.cache_path)
        if age is None or age >= self.hours:
            return 0.0
        return (self.hours - age) * 3600

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="data-refresh", daemon=True)
            self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        delay = self.first_delay()
        log.info("Data refresh every %g hours; next in %.0f minutes", self.hours, delay / 60)
        while not self._stop.wait(delay):
            try:
                self.job()
            except Exception:  # noqa: BLE001 - keep refreshing on schedule
                log.exception("Data refresh failed")
            self.runs += 1
            delay = self.hours * 3600


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(0 if refresh_once() else 1)


if __name__ == "__main__":
    main()
