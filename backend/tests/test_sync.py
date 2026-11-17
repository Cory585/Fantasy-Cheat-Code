import pytest

from app.data.repository import load_player_cache
from app.data.sources import espn, sleeper
from app.data.sources.espn import ProTeam
from app.data.sync import (
    EspnIndex,
    EspnProjection,
    SyncError,
    build_players,
    espn_projections,
    normalize_name,
    write_cache,
)
from app.models.player import Position
from app.models.trade import ScoringFormat

SEASON = 2026
WEEKS = range(10, 12)


def espn_stat(week: int, points: float, receptions: float = 0.0, season: int = SEASON, source: int = 1):
    return {
        "statSourceId": source,
        "statSplitTypeId": 1,
        "seasonId": season,
        "scoringPeriodId": week,
        "appliedTotal": points,
        "stats": {"53": receptions},
    }


def test_normalize_name():
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("A.J. Brown") == "aj brown"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_sleeper_parse_weekly_points_uses_scoring_field():
    rows = [
        {"player_id": "1", "stats": {"pts_ppr": 20.0, "pts_std": 15.0}},
        {"player_id": "2", "stats": {}},
    ]
    assert sleeper.parse_weekly_points(rows, ScoringFormat.PPR) == {"1": 20.0}
    assert sleeper.parse_weekly_points(rows, ScoringFormat.STANDARD) == {"1": 15.0}


def test_espn_ros_points_filters_weeks_season_and_source():
    player = {
        "stats": [
            espn_stat(10, 20.0, receptions=4),
            espn_stat(11, 10.0, receptions=2),
            espn_stat(9, 99.0),  # before start week
            espn_stat(10, 99.0, season=2025),  # last season
            espn_stat(10, 99.0, source=0),  # actual, not projected
        ]
    }
    assert espn.ros_points(player, SEASON, WEEKS, ScoringFormat.PPR) == 30.0
    assert espn.ros_points(player, SEASON, WEEKS, ScoringFormat.HALF_PPR) == 27.0


def test_espn_projections_maps_positions_and_teams():
    raw = [
        {"id": 1, "fullName": "Some Back", "defaultPositionId": 2, "proTeamId": 8, "stats": [espn_stat(10, 12.0)]},
        {"id": -16008, "fullName": "Lions D/ST", "defaultPositionId": 16, "proTeamId": 8, "stats": []},
        {"id": 2, "fullName": "Punter", "defaultPositionId": 7, "proTeamId": 8, "stats": []},
    ]
    result = espn_projections(raw, {8: ProTeam("DET", 8)}, SEASON, WEEKS, ScoringFormat.PPR)
    assert [(p.position, p.team, p.points) for p in result] == [
        (Position.RB, "DET", 12.0),
        (Position.DST, "DET", 0.0),
    ]


def test_espn_index_matching():
    index = EspnIndex(
        [
            EspnProjection(100, "Kenneth Walker III", Position.RB, "SEA", 150.0),
            EspnProjection(200, "Mike Williams", Position.WR, "NYJ", 50.0),
            EspnProjection(201, "Mike Williams", Position.WR, "PIT", 40.0),
            EspnProjection(-1, "Lions D/ST", Position.DST, "DET", 70.0),
        ]
    )
    assert index.find("Anyone", Position.RB, "SEA", 100).espn_id == 100  # by id
    assert index.find("Kenneth Walker", Position.RB, "KC", None).espn_id == 100  # unique name+pos
    assert index.find("Mike Williams", Position.WR, "PIT", None).espn_id == 201  # name+pos+team
    assert index.find("Mike Williams", Position.WR, "LAC", None) is None  # ambiguous
    assert index.find("Detroit Lions", Position.DST, "DET", None).points == 70.0  # D/ST by team


def test_build_players_merges_sources():
    sleeper_players = {
        "10": {"full_name": "Kenneth Walker III", "position": "RB", "team": "SEA", "injury_status": "Questionable"},
        "DET": {"first_name": "Detroit", "last_name": "Lions", "position": "DEF", "team": "DET"},
        "11": {"full_name": "Free Agent", "position": "WR", "team": None},
        "12": {"full_name": "No Projection", "position": "WR", "team": "SEA"},
        "13": {"full_name": "Long Snapper", "position": "LS", "team": "SEA"},
    }
    index = EspnIndex(
        [
            EspnProjection(100, "Kenneth Walker III", Position.RB, "SEA", 200.0),
            EspnProjection(-1, "Lions D/ST", Position.DST, "DET", 60.0),
        ]
    )
    players = build_players(sleeper_players, {"10": 100.0, "DET": 80.0}, index, {"SEA": 8, "DET": 8})

    by_id = {p.id: p for p in players}
    assert set(by_id) == {"10", "DET"}
    walker = by_id["10"]
    assert walker.projection_sources == {"sleeper": 100.0, "espn": 200.0}
    assert walker.ros_projection == 150.0
    assert walker.espn_id == 100
    assert walker.injury_status == "Q"
    assert walker.bye_week == 8
    assert by_id["DET"].name == "Detroit Lions"
    assert by_id["DET"].position == Position.DST


def test_build_players_does_not_give_namesake_another_players_espn_projection():
    # Two active Sleeper WRs share a name; ESPN only lists the one on PIT.
    sleeper_players = {
        "20": {"full_name": "Mike Williams", "position": "WR", "team": "PIT"},
        "21": {"full_name": "Mike Williams", "position": "WR", "team": "LAC"},
    }
    index = EspnIndex([EspnProjection(201, "Mike Williams", Position.WR, "PIT", 40.0)])
    players = build_players(sleeper_players, {"20": 30.0, "21": 10.0}, index, {})

    by_id = {p.id: p for p in players}
    assert by_id["20"].projection_sources == {"sleeper": 30.0, "espn": 40.0}
    assert by_id["21"].projection_sources == {"sleeper": 10.0}
    assert by_id["21"].espn_id is None


def test_cache_round_trip(tmp_path):
    index = EspnIndex([])
    players = build_players(
        {"10": {"full_name": "A Player", "position": "QB", "team": "KC"}}, {"10": 123.4}, index, {}
    )
    path = tmp_path / "players.json"
    write_cache(path, players, SEASON, WEEKS, ScoringFormat.PPR)
    assert load_player_cache(path) == players
    assert load_player_cache(tmp_path / "missing.json") is None


def test_load_player_cache_rejects_a_cache_with_no_players(tmp_path):
    path = tmp_path / "players.json"
    path.write_text('{"season": 2026, "weeks": [19, 18], "players": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="no players"):
        load_player_cache(path)


def test_sleeper_skips_teams_that_already_played():
    rows = [
        {"player_id": "1", "team": "DET", "stats": {"pts_ppr": 20.0}},
        {"player_id": "2", "team": "CHI", "stats": {"pts_ppr": 10.0}},
    ]
    assert sleeper.parse_weekly_points(rows, ScoringFormat.PPR, skip_teams=frozenset({"DET"})) == {"2": 10.0}


def test_espn_ros_points_skips_completed_week():
    player = {"stats": [espn_stat(10, 20.0), espn_stat(11, 10.0)]}
    assert espn.ros_points(player, SEASON, WEEKS, ScoringFormat.PPR, skip_week=10) == 10.0


def test_parse_completed_teams_only_counts_final_games():
    def event(completed, away, home):
        return {"competitions": [{"status": {"type": {"completed": completed}},
                                  "competitors": [{"team": {"abbreviation": away}}, {"team": {"abbreviation": home}}]}]}
    data = {"events": [event(True, "WSH", "NYG"), event(False, "SEA", "LAR")]}
    assert espn.parse_completed_teams(data) == {"WAS", "NYG"}


def test_espn_weekly_points_by_week():
    player = {"stats": [espn_stat(10, 20.0, receptions=4), espn_stat(11, 10.0), espn_stat(9, 99.0)]}
    assert espn.weekly_points(player, SEASON, WEEKS, ScoringFormat.HALF_PPR) == {10: 18.0, 11: 10.0}
    assert espn.weekly_points(player, SEASON, WEEKS, ScoringFormat.PPR, skip_week=10) == {11: 10.0}


def test_sleeper_weekly_points_and_totals():
    class FakeSleeper(sleeper.SleeperSource):
        def __init__(self):
            pass

        def weekly_projections(self, season, week):
            return [{"player_id": "1", "team": "DET", "stats": {"pts_ppr": float(week)}}]

    weekly = FakeSleeper().weekly_points(SEASON, WEEKS, ScoringFormat.PPR, played_first_week=frozenset({"DET"}))
    assert weekly == {"1": {11: 11.0}}  # week 10 already played
    assert sleeper.ros_totals(weekly) == {"1": 11.0}


def test_build_players_keeps_weekly_projections_by_source():
    sleeper_players = {"10": {"full_name": "Kenneth Walker III", "position": "RB", "team": "SEA"}}
    index = EspnIndex([EspnProjection(100, "Kenneth Walker III", Position.RB, "SEA", 30.0, {10: 14.0, 11: 16.0})])
    [walker] = build_players(sleeper_players, {"10": 22.0}, index, {}, {"10": {10: 12.0, 11: 10.0}})
    assert walker.weekly_projections == {10: {"sleeper": 12.0, "espn": 14.0}, 11: {"sleeper": 10.0, "espn": 16.0}}
    assert walker.week_projection(10) == 13.0
    assert walker.week_projection(12) is None


def test_weekly_projections_survive_the_cache(tmp_path):
    index = EspnIndex([])
    players = build_players(
        {"10": {"full_name": "A Player", "position": "QB", "team": "KC"}}, {"10": 20.0}, index, {}, {"10": {10: 20.0}}
    )
    path = tmp_path / "players.json"
    write_cache(path, players, SEASON, WEEKS, ScoringFormat.PPR)
    [loaded] = load_player_cache(path)
    assert loaded.weekly_projections == {10: {"sleeper": 20.0}}


def test_cache_keeps_the_season_start_when_given(tmp_path):
    from app.data.repository import load_cache_metadata

    path = tmp_path / "players.json"
    write_cache(path, _players(), SEASON, WEEKS, ScoringFormat.PPR, season_start="2026-09-10")
    assert load_cache_metadata(path)["season_start"] == "2026-09-10"
    write_cache(path, _players(), SEASON, WEEKS, ScoringFormat.PPR)
    assert "season_start" not in load_cache_metadata(path)


def test_season_start_is_only_kept_for_sleepers_current_season():
    from app.data.sync import season_start

    state = {"season": "2026", "week": 11, "season_start_date": "2026-09-10"}
    assert season_start(state, 2026) == "2026-09-10"
    assert season_start(state, 2025) is None  # --season 2025: Sleeper's date is for 2026
    assert season_start({"season_start_date": "2026-09-10"}, 2026) is None


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _players(count: int = 1) -> list:
    raw = {str(i): {"full_name": f"Player {i}", "position": "QB", "team": "KC"} for i in range(count)}
    return build_players(raw, {pid: 100.0 + int(pid) for pid in raw}, EspnIndex([]), {})


def test_sync_past_the_last_week_fails_without_touching_the_cache(tmp_path, monkeypatch):
    from app.data import sync as sync_module

    class PlayoffSleeper:
        def __init__(self, client):
            pass

        def nfl_state(self):
            return {"season": "2026", "week": "19"}

        def players(self):  # pragma: no cover - must not be reached
            raise AssertionError("sync should stop before fetching players")

    monkeypatch.setattr(sync_module, "RateLimitedClient", _FakeClient)
    monkeypatch.setattr(sync_module, "SleeperSource", PlayoffSleeper)
    path = tmp_path / "players.json"
    write_cache(path, _players(), SEASON, WEEKS, ScoringFormat.PPR)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SyncError, match="past end week 18"):
        sync_module.sync(path, ScoringFormat.PPR, end_week=18)
    assert path.read_text(encoding="utf-8") == before


def test_write_cache_refuses_an_empty_player_list(tmp_path):
    path = tmp_path / "players.json"
    with pytest.raises(SyncError):
        write_cache(path, [], SEASON, WEEKS, ScoringFormat.PPR)
    assert not path.exists()

    write_cache(path, _players(), SEASON, WEEKS, ScoringFormat.PPR)
    with pytest.raises(SyncError):
        write_cache(path, [], SEASON, WEEKS, ScoringFormat.PPR, force=True)
    assert len(load_player_cache(path)) == 1


def test_write_cache_refuses_a_much_smaller_cache_unless_forced(tmp_path):
    path = tmp_path / "players.json"
    many = _players(10)
    write_cache(path, many, SEASON, WEEKS, ScoringFormat.PPR)

    with pytest.raises(SyncError, match="down from 10"):
        write_cache(path, many[:4], SEASON, WEEKS, ScoringFormat.PPR)
    assert len(load_player_cache(path)) == 10

    write_cache(path, many[:5], SEASON, WEEKS, ScoringFormat.PPR)  # half is still fine
    assert len(load_player_cache(path)) == 5
    write_cache(path, many[:1], SEASON, WEEKS, ScoringFormat.PPR, force=True)
    assert len(load_player_cache(path)) == 1
