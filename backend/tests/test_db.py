from app.data import db


def test_connect_creates_schema(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"teams", "players", "games", "player_week_stats", "injuries", "espn_player_market"} <= tables


def test_upsert_replaces_rows_with_same_key(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    row = {"abbrev": "DET", "espn_id": 8, "location": "Detroit", "name": "Lions",
           "display_name": "Detroit Lions", "color": "0076b6", "logo_url": None}
    assert db.upsert(conn, "teams", [row]) == 1
    db.upsert(conn, "teams", [{**row, "color": "b0b7bc"}])
    rows = conn.execute("SELECT abbrev, color FROM teams").fetchall()
    assert [tuple(r) for r in rows] == [("DET", "b0b7bc")]


def test_upsert_empty_is_noop(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    assert db.upsert(conn, "teams", []) == 0


def test_record_run(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.record_run(conn, "games", 2026, 285)
    db.record_run(conn, "games", 2026, 300)
    row = conn.execute("SELECT rows FROM collection_runs WHERE dataset='games' AND season=2026").fetchone()
    assert row["rows"] == 300


def test_connect_adds_new_columns_to_old_databases(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # The games table as first released, before the coach columns existed.
    old.execute("CREATE TABLE games (game_id TEXT PRIMARY KEY, season INTEGER NOT NULL, week INTEGER, espn_id TEXT)")
    old.commit()
    old.close()
    conn = db.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    assert {"away_coach", "home_coach"} <= columns


def test_connect_renames_relocated_teams_in_old_databases(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    db.upsert(conn, "games", [{"game_id": "2019_01_DEN_OAK", "season": 2019, "week": 1,
                               "away_team": "DEN", "home_team": "OAK"},
                              {"game_id": "2015_01_STL_SD", "season": 2015, "week": 1,
                               "away_team": "STL", "home_team": "SD"}])
    db.upsert(conn, "injuries", [{"gsis_id": "00-1", "season": 2019, "week": 1, "season_type": "REG",
                                  "team": "OAK"}])
    conn.close()
    conn = db.connect(path)
    games = {r["game_id"]: (r["away_team"], r["home_team"]) for r in conn.execute("SELECT * FROM games")}
    assert games == {"2019_01_DEN_OAK": ("DEN", "LV"), "2015_01_STL_SD": ("LAR", "LAC")}
    assert conn.execute("SELECT team FROM injuries").fetchone()[0] == "LV"
