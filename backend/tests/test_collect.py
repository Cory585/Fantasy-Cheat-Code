from datetime import date

from app.data import collect, db


def test_current_season_rolls_over_in_march():
    assert collect.current_season(date(2026, 11, 9)) == 2026
    assert collect.current_season(date(2027, 1, 20)) == 2026  # playoffs belong to last season
    assert collect.current_season(date(2027, 3, 1)) == 2027


def test_collect_continues_after_a_failed_dataset(tmp_path, monkeypatch):
    def fake_collect_dataset(client, dataset, season):
        if dataset == "games":
            raise RuntimeError("HTTP 503")
        return "teams", [{"abbrev": "DET", "espn_id": 8, "location": None, "name": None,
                          "display_name": "Detroit Lions", "color": None, "logo_url": None}]

    monkeypatch.setattr(collect, "collect_dataset", fake_collect_dataset)
    conn = db.connect(tmp_path / "test.db")
    results = collect.collect(["games", "teams"], [2026], conn)

    assert isinstance(results["games:2026"], RuntimeError)
    assert results["teams:2026"] == 1
    runs = {r["dataset"] for r in conn.execute("SELECT dataset FROM collection_runs")}
    assert runs == {"teams"}  # failed datasets aren't recorded as collected


def test_every_dataset_is_handled():
    assert set(collect.ALL_DATASETS) == {"teams", "espn_market", "players", "games", "player_week_stats",
                                         "team_week_stats", "snap_counts", "injuries", "combine", "playcalling",
                                         "weather"}


def test_parse_seasons():
    assert collect.parse_seasons("2026") == [2026]
    assert collect.parse_seasons("2022-2024") == [2022, 2023, 2024]
    assert collect.parse_seasons("2025,2023") == [2023, 2025]


def test_seasonless_datasets_run_once(tmp_path, monkeypatch):
    calls = []

    def fake(client, dataset, season):
        calls.append((dataset, season))
        return "teams", []

    monkeypatch.setattr(collect, "collect_dataset", fake)
    collect.collect(["games", "injuries"], [2024, 2025], db.connect(tmp_path / "t.db"))
    assert calls == [("games", 2025), ("injuries", 2024), ("injuries", 2025)]


def test_a_dataset_can_fill_several_tables(tmp_path, monkeypatch):
    rows = {"team_game_offense": [{"game_id": "g", "team": "KC", "season": 2026, "week": 1, "season_type": "REG"}],
            "player_game_usage": [{"game_id": "g", "gsis_id": "p", "season": 2026, "week": 1, "season_type": "REG"}]}
    monkeypatch.setattr(collect, "collect_dataset", lambda client, dataset, season: rows)
    conn = db.connect(tmp_path / "t.db")
    assert collect.collect(["playcalling"], [2026], conn) == {"playcalling:2026": 2}
    assert conn.execute("SELECT count(*) FROM player_game_usage").fetchone()[0] == 1
