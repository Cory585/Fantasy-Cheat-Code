"""SQLite cache of ESPN player profiles (`backend/data/profiles.db`, gitignored).

Kept separate from `fantasy.db`: the API reloads player data whenever that file changes,
and profiles are written while the API runs.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_profiles (
    espn_id        INTEGER PRIMARY KEY,
    status         TEXT NOT NULL,      -- 'ok' or 'not_found'
    fetched_at     REAL NOT NULL,      -- unix time
    bio            TEXT,               -- JSON
    nfl_stats      TEXT,               -- JSON
    college_stats  TEXT                -- JSON
);
"""


@dataclass
class CachedProfile:
    espn_id: int
    status: str
    fetched_at: float
    bio: dict | None
    nfl_stats: list
    college_stats: list


class ProfileCache:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=5)
        if not self._ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self._ready = True
        return conn

    def get(self, espn_id: int) -> CachedProfile | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status, fetched_at, bio, nfl_stats, college_stats FROM player_profiles WHERE espn_id = ?",
                    (espn_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        status, fetched_at, bio, nfl, college = row
        try:
            return CachedProfile(espn_id, status, fetched_at, json.loads(bio) if bio else None,
                                 json.loads(nfl or "[]"), json.loads(college or "[]"))
        except ValueError:
            return None  # a corrupt row is treated as a miss and overwritten on the next fetch

    def put(self, profile: CachedProfile) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO player_profiles VALUES (?, ?, ?, ?, ?, ?)",
                        (profile.espn_id, profile.status, profile.fetched_at,
                         json.dumps(profile.bio) if profile.bio is not None else None,
                         json.dumps(profile.nfl_stats), json.dumps(profile.college_stats)),
                    )
            finally:
                conn.close()
