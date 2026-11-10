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
