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
    espn_id      TEXT,
    away_coach   TEXT,
    home_coach   TEXT
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

-- NFL Scouting Combine results since 2000. Joins to players by pfr_id (most rows have one).
CREATE TABLE IF NOT EXISTS combine (
    combine_id   TEXT PRIMARY KEY,   -- pfr_id, or season:name:position:school when there's none
    season       INTEGER NOT NULL,
    pfr_id       TEXT,
    cfb_id       TEXT,
    name         TEXT NOT NULL,
    position     TEXT,
    school       TEXT,
    draft_year   INTEGER,
    draft_team   TEXT,
    draft_round  INTEGER,
    draft_pick   INTEGER,
    height_in    REAL,
    weight_lb    REAL,
    forty        REAL,
    bench        INTEGER,
    vertical     REAL,
    broad_jump   INTEGER,
    cone         REAL,
    shuttle      REAL
);
CREATE INDEX IF NOT EXISTS combine_pfr_id ON combine (pfr_id);
CREATE INDEX IF NOT EXISTS combine_position ON combine (position);

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

-- Offense play-calling and efficiency per game, summarized from play-by-play.
CREATE TABLE IF NOT EXISTS team_game_offense (
    game_id            TEXT NOT NULL,
    team               TEXT NOT NULL,  -- the offense
    season             INTEGER NOT NULL,
    week               INTEGER NOT NULL,
    season_type        TEXT NOT NULL,
    opponent           TEXT,
    plays              INTEGER,
    dropbacks          INTEGER,
    sacks              INTEGER,
    interceptions      INTEGER,
    fumbles_lost       INTEGER,
    dropback_epa       REAL,  -- summed; divide by dropbacks
    neutral_plays      INTEGER,
    neutral_dropbacks  INTEGER,
    pass_oe_sum        REAL,  -- summed neutral pass rate over expected (0-100 scale)
    pass_oe_n          INTEGER,
    PRIMARY KEY (game_id, team)
);
CREATE INDEX IF NOT EXISTS team_game_offense_season ON team_game_offense (season, week);

-- How each player was used per game, from play-by-play (targets by field location, deep,
-- red-zone, end-zone, and third-down targets; red-zone and goal-line carries).
CREATE TABLE IF NOT EXISTS player_game_usage (
    game_id             TEXT NOT NULL,
    gsis_id             TEXT NOT NULL,
    season              INTEGER NOT NULL,
    week                INTEGER NOT NULL,
    season_type         TEXT NOT NULL,
    team                TEXT,
    targets             INTEGER,
    targets_left        INTEGER,
    targets_middle      INTEGER,  -- between the numbers: mostly slot receivers and tight ends
    targets_right       INTEGER,
    air_yards           REAL,
    deep_targets        INTEGER,  -- 20+ air yards
    behind_los_targets  INTEGER,  -- at or behind the line (screens, swings)
    red_zone_targets    INTEGER,
    end_zone_targets    INTEGER,
    third_down_targets  INTEGER,  -- third and fourth down
    carries             INTEGER,
    red_zone_carries    INTEGER,
    goal_line_carries   INTEGER,  -- inside the 5
    PRIMARY KEY (game_id, gsis_id)
);
CREATE INDEX IF NOT EXISTS player_game_usage_season ON player_game_usage (season, gsis_id);

-- Forecast weather during upcoming games (kickoff + 3 hours). Joins to games.espn_id.
CREATE TABLE IF NOT EXISTS game_weather (
    espn_event_id      TEXT PRIMARY KEY,
    season             INTEGER,
    week               INTEGER,
    away_team          TEXT,
    home_team          TEXT,
    kickoff            TEXT,
    venue              TEXT,
    city               TEXT,
    indoor             INTEGER,
    temperature_f      REAL,
    wind_mph           REAL,
    precip_probability REAL,
    precipitation_in   REAL,
    fetched_at         TEXT NOT NULL
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


# Columns added after a table was first released: (table, column, type). Applied to
# existing databases on connect, since CREATE TABLE IF NOT EXISTS won't add them.
MIGRATIONS = [
    ("games", "away_coach", "TEXT"),
    ("games", "home_coach", "TEXT"),
]


# Team columns from sources that used a relocated franchise's old code (see
# app.data.sources.nflverse.TEAM_ALIASES), renamed in databases collected before the fix.
TEAM_RENAMES = {"OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR"}
TEAM_COLUMNS = [("games", "away_team"), ("games", "home_team"), ("injuries", "team"),
                ("snap_counts", "team"), ("snap_counts", "opponent")]


def connect(path: Path) -> sqlite3.Connection:
    """Open (and create if needed) the database, with the schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # lets the API read while a collection writes
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, kind in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    cases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in TEAM_RENAMES.items())
    olds = ", ".join(f"'{old}'" for old in TEAM_RENAMES)
    for table, column in TEAM_COLUMNS:
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            continue  # a very old table without the column; nothing to rename
        conn.execute(f"UPDATE OR REPLACE {table} SET {column} = CASE {column} {cases} END "  # noqa: S608 - fixed table and column lists
                     f"WHERE {column} IN ({olds})")
    conn.commit()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[Mapping]) -> int:
    """Insert rows, replacing any with the same primary key. Returns the row count."""
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608 - tables and columns come from our parsers, values are bound
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def record_run(conn: sqlite3.Connection, dataset: str, season: int, rows: int) -> None:
    upsert(
        conn,
        "collection_runs",
        [{"dataset": dataset, "season": season, "rows": rows, "ran_at": datetime.now(timezone.utc).isoformat()}],
    )


# The collection_runs row (dataset, season) recording when the last whole collection finished.
# The API reloads off it rather than off every commit, so it never serves a half-done collection.
COMPLETED_RUN = ("*", 0)


def record_completed_run(conn: sqlite3.Connection, rows: int) -> None:
    """Mark a collection as finished (with the total rows it wrote)."""
    record_run(conn, COMPLETED_RUN[0], COMPLETED_RUN[1], rows)


def last_completed_run(conn: sqlite3.Connection) -> str | None:
    """When the last whole collection finished (ISO timestamp), or None if none is recorded."""
    try:
        row = conn.execute("SELECT ran_at FROM collection_runs WHERE dataset = ? AND season = ?",
                           COMPLETED_RUN).fetchone()
    except sqlite3.OperationalError:  # a database from before collection_runs existed
        return None
    return row[0] if row else None
