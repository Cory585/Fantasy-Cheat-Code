"""SQLite storage for collected player and team data.

SQLite ships with Python, needs no server, and stores everything in one file
(`backend/data/fantasy.db`, gitignored). Wide stat lines (nflverse has ~150 columns)
are kept in a JSON `stats` column with the commonly queried fields promoted to real
columns; SQLite's JSON functions can still query the rest, e.g.
    SELECT json_extract(stats, '$.targets') FROM player_week_stats

Tables are keyed so re-running a collection replaces rows instead of duplicating them.
"""

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    abbrev        TEXT PRIMARY KEY,
    espn_id       INTEGER,
    location      TEXT,
    name          TEXT,
    display_name  TEXT,
    color         TEXT,
    logo_url      TEXT
);

-- One row per NFL player, with IDs for every provider (the join key across sources).
CREATE TABLE IF NOT EXISTS players (
    gsis_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    position            TEXT,
    team                TEXT,
    status              TEXT,
    espn_id             INTEGER,
    pfr_id              TEXT,
    pff_id              TEXT,
    birth_date          TEXT,
    height              REAL,
    weight              REAL,
    years_of_experience INTEGER,
    rookie_season       INTEGER,
    draft_year          INTEGER,
    draft_round         INTEGER,
    draft_pick          INTEGER,
    headshot_url        TEXT
);
CREATE INDEX IF NOT EXISTS players_espn_id ON players (espn_id);
CREATE INDEX IF NOT EXISTS players_pfr_id ON players (pfr_id);

-- Schedule and results, with Vegas lines (useful for kicker/defense and start/sit).
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    season       INTEGER NOT NULL,
    game_type    TEXT,
    week         INTEGER,
    gameday      TEXT,
    gametime     TEXT,
    away_team    TEXT,
    home_team    TEXT,
    away_score   INTEGER,
    home_score   INTEGER,
    spread_line  REAL,
    total_line   REAL,
    roof         TEXT,
    surface      TEXT,
    temp         REAL,
    wind         REAL,
    div_game     INTEGER,
    espn_id      TEXT
);
CREATE INDEX IF NOT EXISTS games_season_week ON games (season, week);

CREATE TABLE IF NOT EXISTS player_week_stats (
    gsis_id            TEXT NOT NULL,
    season             INTEGER NOT NULL,
    week               INTEGER NOT NULL,
    season_type        TEXT NOT NULL,
    name               TEXT,
    position           TEXT,
    team               TEXT,
    opponent           TEXT,
    fantasy_points     REAL,
    fantasy_points_ppr REAL,
    stats              TEXT NOT NULL,  -- JSON: every other stat column
    PRIMARY KEY (gsis_id, season, week, season_type)
);

CREATE TABLE IF NOT EXISTS team_week_stats (
    team         TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    season_type  TEXT NOT NULL,
    opponent     TEXT,
    stats        TEXT NOT NULL,  -- JSON
    PRIMARY KEY (team, season, week, season_type)
);

CREATE TABLE IF NOT EXISTS snap_counts (
    pfr_id         TEXT NOT NULL,
    game_id        TEXT NOT NULL,
    season         INTEGER NOT NULL,
    week           INTEGER,
    name           TEXT,
    position       TEXT,
    team           TEXT,
    opponent       TEXT,
    offense_snaps  REAL,
    offense_pct    REAL,
    defense_snaps  REAL,
    defense_pct    REAL,
    st_snaps       REAL,
    st_pct         REAL,
    PRIMARY KEY (pfr_id, game_id)
);

CREATE TABLE IF NOT EXISTS injuries (
    gsis_id          TEXT NOT NULL,
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    season_type      TEXT NOT NULL,
    team             TEXT NOT NULL,
    name             TEXT,
    position         TEXT,
    report_status    TEXT,
    practice_status  TEXT,
    primary_injury   TEXT,
    PRIMARY KEY (gsis_id, season, week, season_type, team)
);

-- Daily snapshots from ESPN Fantasy, so trends (rostered %, ADP) can be tracked over time.
CREATE TABLE IF NOT EXISTS espn_player_market (
    espn_id          INTEGER NOT NULL,
    snapshot_date    TEXT NOT NULL,
    name             TEXT,
    position         TEXT,
    team             TEXT,
    percent_owned    REAL,
    percent_started  REAL,
    percent_change   REAL,
    adp              REAL,
    auction_value    REAL,
    rank_standard    INTEGER,
    rank_ppr         INTEGER,
    PRIMARY KEY (espn_id, snapshot_date)
);

-- When each dataset was last collected, and how many rows it produced.
CREATE TABLE IF NOT EXISTS collection_runs (
    dataset    TEXT NOT NULL,
    season     INTEGER NOT NULL,
    rows       INTEGER NOT NULL,
    ran_at     TEXT NOT NULL,
    PRIMARY KEY (dataset, season)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and create if needed) the database, with the schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # lets the API read while a collection writes
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[Mapping]) -> int:
    """Insert rows, replacing any with the same primary key. Returns the row count."""
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def record_run(conn: sqlite3.Connection, dataset: str, season: int, rows: int) -> None:
    upsert(
        conn,
        "collection_runs",
        [{"dataset": dataset, "season": season, "rows": rows, "ran_at": datetime.now(timezone.utc).isoformat()}],
    )
