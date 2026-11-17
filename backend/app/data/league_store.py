"""SQLite store of the leagues on each profile (`backend/data/leagues.db`, gitignored).

There are no accounts. A profile is a random key the browser makes and keeps; the server
stores only its SHA-256 (`owner`), and every query is scoped to it. Kept apart from
`fantasy.db`, which the API reloads whenever it changes.

Unlike the profile cache, failures here aren't hidden: a league that silently didn't save
would lose someone's roster. They raise `LeagueStoreError`, which the API turns into a 503.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.models.league import League

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,      -- SHA-256 hex of the profile key
    name        TEXT NOT NULL,
    slots       TEXT NOT NULL,      -- JSON {slot: count}
    roster      TEXT NOT NULL,      -- JSON [player id]
    created_at  REAL NOT NULL,      -- unix time
    updated_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1  -- goes up by one with each save
);
CREATE INDEX IF NOT EXISTS leagues_by_owner ON leagues (owner, created_at);
"""


class LeagueStoreError(Exception):
    """The store couldn't be read or written."""


class LeagueConflict(Exception):
    """The league was saved since the version the client last saw. Holds the current league."""

    def __init__(self, current: League):
        super().__init__(f"league {current.id} is at version {current.version}")
        self.current = current


class LeagueLimitReached(Exception):
    """The profile already has the most leagues allowed."""


class StoreFull(Exception):
    """The server holds the most leagues it's configured to keep."""


def owner_for(profile_key: str) -> str:
    return hashlib.sha256(profile_key.encode()).hexdigest()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _league(row) -> League:
    league_id, name, slots, roster, created, updated, version = row
    return League(id=league_id, name=name, slots=json.loads(slots), roster=json.loads(roster),
                  created=_iso(created), updated=_iso(updated), version=version)


COLUMNS = "id, name, slots, roster, created_at, updated_at, version"


def add_version_column(conn: sqlite3.Connection) -> None:
    """Gives a database made before leagues had versions its `version` column. Safe to run
    more than once, and when another process adds the column at the same moment."""
    try:
        conn.execute("ALTER TABLE leagues ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError as error:
        if "duplicate column" not in str(error).lower():
            raise


class LeagueStore:
    def __init__(self, path: Path, max_total: int = 100_000):
        self._path = path
        self._max_total = max_total
        self._lock = threading.Lock()
        self._migrated = False

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if not self._migrated:
                add_version_column(conn)
                self._migrated = True
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    def _run(self, work):
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = work(conn)
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
                    conn.execute("COMMIT")
                    return result
                finally:
                    conn.close()
        except (sqlite3.Error, OSError, ValueError) as error:
            raise LeagueStoreError(str(error)) from error

    def list(self, owner: str) -> list[League]:
        rows = self._run(lambda conn: conn.execute(
            f"SELECT {COLUMNS} FROM leagues WHERE owner = ? ORDER BY created_at, id", (owner,)).fetchall())  # noqa: S608 - COLUMNS is a constant
        return [_league(row) for row in rows]

    def get(self, owner: str, league_id: str) -> League | None:
        row = self._run(lambda conn: conn.execute(
            f"SELECT {COLUMNS} FROM leagues WHERE owner = ? AND id = ?", (owner, league_id)).fetchone())  # noqa: S608 - COLUMNS is a constant
        return _league(row) if row else None

    def create(self, owner: str, name: str, slots: dict[str, int], roster: list[str], limit: int) -> League:
        """Adds a league. The count and the insert share a transaction, so two requests at
        once can't both slip in under the limit."""

        def work(conn):
            (count,) = conn.execute("SELECT COUNT(*) FROM leagues WHERE owner = ?", (owner,)).fetchone()
            if count >= limit:
                raise LeagueLimitReached
            (total,) = conn.execute("SELECT COUNT(*) FROM leagues").fetchone()
            if total >= self._max_total:
                raise StoreFull
            now = time.time()
            league_id = secrets.token_urlsafe(9)
            conn.execute("INSERT INTO leagues (id, owner, name, slots, roster, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (league_id, owner, name, json.dumps(slots), json.dumps(roster), now, now))
            return conn.execute(f"SELECT {COLUMNS} FROM leagues WHERE id = ?", (league_id,)).fetchone()  # noqa: S608 - COLUMNS is a constant

        return _league(self._run(work))

    def update(self, owner: str, league_id: str, name: str, slots: dict[str, int], roster: list[str],
               expected_version: int | None = None) -> League | None:
        """Replaces a league (None if it isn't on the profile). With `expected_version`, the save
        only goes through if the league is still at that version; otherwise it raises
        `LeagueConflict` with the current league. The check and the write share a transaction."""

        def work(conn):
            current = conn.execute(
                f"SELECT {COLUMNS} FROM leagues WHERE owner = ? AND id = ?", (owner, league_id)).fetchone()  # noqa: S608 - COLUMNS is a constant
            if current is None:
                return None
            if expected_version is not None and current[-1] != expected_version:
                raise LeagueConflict(_league(current))
            conn.execute(
                "UPDATE leagues SET name = ?, slots = ?, roster = ?, updated_at = ?, version = version + 1 "
                "WHERE owner = ? AND id = ?",
                (name, json.dumps(slots), json.dumps(roster), time.time(), owner, league_id))
            return conn.execute(f"SELECT {COLUMNS} FROM leagues WHERE id = ?", (league_id,)).fetchone()  # noqa: S608 - COLUMNS is a constant

        row = self._run(work)
        return _league(row) if row else None

    def delete(self, owner: str, league_id: str) -> bool:
        return self._run(lambda conn: conn.execute(
            "DELETE FROM leagues WHERE owner = ? AND id = ?", (owner, league_id)).rowcount) > 0
