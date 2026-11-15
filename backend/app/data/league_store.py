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
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS leagues_by_owner ON leagues (owner, created_at);
"""


class LeagueStoreError(Exception):
    """The store couldn't be read or written."""


class LeagueLimitReached(Exception):
    """The profile already has the most leagues allowed."""


class StoreFull(Exception):
    """The server holds the most leagues it's configured to keep."""


def owner_for(profile_key: str) -> str:
    return hashlib.sha256(profile_key.encode()).hexdigest()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _league(row) -> League:
    league_id, name, slots, roster, created, updated = row
    return League(id=league_id, name=name, slots=json.loads(slots), roster=json.loads(roster),
                  created=_iso(created), updated=_iso(updated))


COLUMNS = "id, name, slots, roster, created_at, updated_at"


class LeagueStore:
    def __init__(self, path: Path, max_total: int = 100_000):
        self._path = path
        self._max_total = max_total
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
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
            f"SELECT {COLUMNS} FROM leagues WHERE owner = ? ORDER BY created_at, id", (owner,)).fetchall())
        return [_league(row) for row in rows]

    def get(self, owner: str, league_id: str) -> League | None:
        row = self._run(lambda conn: conn.execute(
            f"SELECT {COLUMNS} FROM leagues WHERE owner = ? AND id = ?", (owner, league_id)).fetchone())
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
            conn.execute("INSERT INTO leagues VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (league_id, owner, name, json.dumps(slots), json.dumps(roster), now, now))
            return conn.execute(f"SELECT {COLUMNS} FROM leagues WHERE id = ?", (league_id,)).fetchone()

        return _league(self._run(work))

    def update(self, owner: str, league_id: str, name: str, slots: dict[str, int], roster: list[str]) -> League | None:
        def work(conn):
            changed = conn.execute(
                "UPDATE leagues SET name = ?, slots = ?, roster = ?, updated_at = ? WHERE owner = ? AND id = ?",
                (name, json.dumps(slots), json.dumps(roster), time.time(), owner, league_id)).rowcount
            if not changed:
                return None
            return conn.execute(f"SELECT {COLUMNS} FROM leagues WHERE id = ?", (league_id,)).fetchone()

        row = self._run(work)
        return _league(row) if row else None

    def delete(self, owner: str, league_id: str) -> bool:
        return self._run(lambda conn: conn.execute(
            "DELETE FROM leagues WHERE owner = ? AND id = ?", (owner, league_id)).rowcount) > 0
