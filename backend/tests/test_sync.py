from app.data.repository import load_player_cache
from app.data.sources import espn, sleeper
from app.data.sources.espn import ProTeam
from app.data.sync import (
    EspnIndex,
    EspnProjection,
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
