"""Keep the synced and collected data fresh.

The sync (player pool, projections, injury report) and the collect (stats, schedules, ADP,
weather) are the only jobs that read outside providers. Run by hand, the data falls a week
behind by the next game day: rest-of-season totals and the injury report go stale on every page.

Once, from `backend/` (e.g. from cron or Task Scheduler):

    python -m app.data.refresh

Or while the API runs: set AUTO_REFRESH_HOURS (e.g. 6) and a background thread runs both jobs
that often. Requests keep reading the current files while a refresh runs; both jobs swap their
results in when done, and the API reloads files that changed.

Each refresh:
  * syncs in the scoring format the current cache was synced in (PPR without a cache), so a
    half-PPR setup stays half PPR;
  * collects the datasets that change during the week every time, and the heavy or slow-moving
    ones (play-by-play, rosters, combine, teams) at most once a day;
  * records when it ran in `refresh_state.json` next to the cache, so a restart (or a sync that
    keeps failing, as it does past the last week of the season) doesn't refresh again at once;
  * holds `refresh.lock` next to the cache while it runs, so several API workers, or a cron run
    alongside them, never refresh at the same time.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.data import db
from app.data.collect import ALL_DATASETS, collect, current_season
from app.data.sync import SyncError, sync
from app.models.trade import ScoringFormat

log = logging.getLogger(__name__)

# Collected at most once a day: large (play-by-play is ~19 MB) or barely changing in season.
DAILY_DATASETS = ("teams", "players", "combine", "playcalling")
FREQUENT_DATASETS = tuple(d for d in ALL_DATASETS if d not in DAILY_DATASETS)
DAILY_HOURS = 24
# A lock older than this was left by a run that died; it's taken over.
STALE_LOCK_SECONDS = 3 * 3600


def _state_path(cache_path: Path) -> Path:
    return cache_path.with_name("refresh_state.json")


def _lock_path(cache_path: Path) -> Path:
    return cache_path.with_name("refresh.lock")


def read_state(cache_path: Path) -> dict:
    try:
        state = json.loads(_state_path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(cache_path: Path, state: dict) -> None:
    path = _state_path(cache_path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        log.warning("Couldn't record the refresh in %s: %s", path, error)


def _hours_since(stamp: object, now: datetime) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 3600


def cached_scoring(cache_path: Path) -> ScoringFormat:
    """The scoring format the current cache was synced in (the default without one)."""
    try:
        return ScoringFormat(json.loads(cache_path.read_text(encoding="utf-8"))["scoring"])
    except (OSError, ValueError, KeyError, TypeError):
        return ScoringFormat(settings.default_scoring)


def cache_age_hours(path: Path, now: datetime | None = None) -> float | None:
    """Hours since the player cache at `path` was synced, or None if it's missing or unreadable."""
    try:
        synced_at = json.loads(path.read_text(encoding="utf-8"))["synced_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return _hours_since(synced_at, now or datetime.now(timezone.utc))


def hours_since_refresh(cache_path: Path, now: datetime | None = None) -> float | None:
    """Hours since the last refresh ran (worked or not), or since the cache was synced by hand,
    whichever is more recent. None when neither is known."""
    now = now or datetime.now(timezone.utc)
    ages = [a for a in (_hours_since(read_state(cache_path).get("last_run"), now), cache_age_hours(cache_path, now))
            if a is not None]
    return min(ages) if ages else None


class _Lock:
    """An exclusive lock file, so only one process refreshes at a time."""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self) -> bool:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    stale = time.time() - self.path.stat().st_mtime > STALE_LOCK_SECONDS
                except OSError:
                    continue  # removed meanwhile: try again
                if not stale:
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            except OSError as error:
                log.warning("Couldn't take the refresh lock %s: %s", self.path, error)
                return False
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def refresh_once(cache_path: Path | None = None, database_path: Path | None = None) -> bool:
    """Sync, then collect the current season. True if both worked; a failure is logged, and the
    files it would have replaced keep serving. False without running if another refresh holds
    the lock."""
    cache_path = cache_path or settings.player_cache_path
    database_path = database_path or settings.database_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _Lock(_lock_path(cache_path))
    if not lock.acquire():
        log.info("Refresh skipped: another refresh is running (%s)", lock.path)
        return False
    try:
        now = datetime.now(timezone.utc)
        state = read_state(cache_path)
        ok = True
        try:
            sync(cache_path, cached_scoring(cache_path))
        except SyncError as error:
            log.error("Refresh: sync failed: %s", error)
            ok = False
        except Exception:  # noqa: BLE001 - a network or provider error mustn't kill the refresher
            log.exception("Refresh: sync failed")
            ok = False

        since_full = _hours_since(state.get("last_full_collect"), now)
        full = since_full is None or since_full >= DAILY_HOURS
        datasets = list(ALL_DATASETS if full else FREQUENT_DATASETS)
        try:
            conn = db.connect(database_path)
            try:
                results = collect(datasets, [current_season()], conn)
            finally:
                conn.close()
            failed = [key for key, result in results.items() if isinstance(result, Exception)]
            if failed:
                log.error("Refresh: collect failed for %s", ", ".join(failed))
                ok = False
        except Exception:  # noqa: BLE001
            log.exception("Refresh: collect failed")
            ok = False
            full = False

        state["last_run"] = now.isoformat()
        if full:
            state["last_full_collect"] = now.isoformat()
        _write_state(cache_path, state)
        return ok
    finally:
        lock.release()


class Refresher:
    """Runs `job` every `hours` on a daemon thread. The first run is right away when nothing has
    refreshed (or synced) the data for `hours`, otherwise when that comes due."""

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
        age = hours_since_refresh(self.cache_path)
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
