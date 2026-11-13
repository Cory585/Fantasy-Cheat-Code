import json

from app.data import db
from app.data.sources import nflverse


def test_parse_players_maps_ids_and_skips_rows_without_gsis():
    text = (
        "gsis_id,display_name,position,latest_team,status,espn_id,pfr_id,height,weight,draft_round\n"
        "00-0039139,Jahmyr Gibbs,RB,DET,ACT,4429795,GibbJa01,69,200,1\n"
        ",No Id,WR,,,,,,,\n"
    )
    rows = nflverse.parse_players(text)
    assert len(rows) == 1
    gibbs = rows[0]
    assert gibbs["espn_id"] == 4429795
    assert gibbs["pfr_id"] == "GibbJa01"
    assert gibbs["draft_round"] == 1
    assert gibbs["birth_date"] is None  # missing column -> None


def test_parse_games_handles_missing_scores_and_lines():
    text = (
        "game_id,season,game_type,week,away_team,home_team,away_score,home_score,spread_line,total_line,espn\n"
        "2026_10_WAS_NYG,2026,REG,10,WAS,NYG,NA,NA,2.5,42.5,401873052\n"
    )
    (game,) = nflverse.parse_games(text)
    assert game["away_score"] is None
    assert game["spread_line"] == 2.5
    assert game["espn_id"] == "401873052"


def test_parse_player_week_stats_keeps_extra_columns_as_json():
    text = (
        "player_id,player_display_name,position,season,week,season_type,team,opponent_team,"
        "targets,receiving_yards,target_share,fantasy_points,fantasy_points_ppr,headshot_url\n"
        "00-0039139,Jahmyr Gibbs,RB,2026,9,REG,DET,MIN,5,42,0.14,18.2,23.2,https://x\n"
    )
    (row,) = nflverse.parse_player_week_stats(text)
    assert row["fantasy_points_ppr"] == 23.2
    assert row["opponent"] == "MIN"
    stats = json.loads(row["stats"])
    assert stats == {"targets": 5, "receiving_yards": 42, "target_share": 0.14}


def test_parse_injuries_falls_back_to_practice_injury():
    text = (
        "season,season_type,team,week,gsis_id,position,full_name,report_primary_injury,"
        "report_status,practice_primary_injury,practice_status\n"
        "2026,REG,WAS,9,00-1,WR,Some Player,,Questionable,Hamstring,Limited Participation in Practice\n"
    )
    (row,) = nflverse.parse_injuries(text)
    assert row["primary_injury"] == "Hamstring"
    assert row["report_status"] == "Questionable"


def test_parsed_rows_fit_the_schema(tmp_path):
    """Every parser's columns must match its table, or the upsert fails."""
    samples = {
        "players": "gsis_id,display_name\n00-1,A Player\n",
        "games": "game_id,season,week\n2026_01_A_B,2026,1\n",
        "player_week_stats": "player_id,season,week\n00-1,2026,1\n",
        "team_week_stats": "team,season,week\nDET,2026,1\n",
        "snap_counts": "pfr_player_id,game_id,season\nAbc01,2026_01_A_B,2026\n",
        "injuries": "gsis_id,season,week,team\n00-1,2026,1,DET\n",
        "combine": "season,player_name,pos\n2023,A Player,RB\n",
    }
    conn = db.connect(tmp_path / "test.db")
    for dataset, text in samples.items():
        assert db.upsert(conn, dataset, nflverse.PARSERS[dataset](text)) == 1, dataset


def test_dataset_urls_cover_every_parser():
    assert set(nflverse.dataset_urls(2026)) == set(nflverse.PARSERS)


def test_relocated_franchises_use_their_current_code():
    games = nflverse.parse_games("game_id,season,week,away_team,home_team\n"
                                 "2019_01_DEN_OAK,2019,1,DEN,OAK\n2015_01_STL_SD,2015,1,STL,SD\n")
    assert [(g["away_team"], g["home_team"]) for g in games] == [("DEN", "LV"), ("LAR", "LAC")]
    injuries = nflverse.parse_injuries("gsis_id,season,week,game_type,team,full_name\n00-1,2019,1,REG,OAK,X\n")
    assert injuries[0]["team"] == "LV"


def test_team_abbreviations_match_the_rest_of_the_app():
    games = nflverse.parse_games("game_id,season,week,away_team,home_team\n2026_10_LA_SF,2026,10,LA,SF\n")
    assert (games[0]["away_team"], games[0]["home_team"]) == ("LAR", "SF")
    stats = nflverse.parse_player_week_stats(
        "player_id,season,week,team,opponent_team\n00-1,2026,1,LA,SEA\n00-2,2026,1,SEA,LA\n"
    )
    assert [(r["team"], r["opponent"]) for r in stats] == [("LAR", "SEA"), ("SEA", "LAR")]
    teams = nflverse.parse_team_week_stats("team,season,week,opponent_team\nLA,2026,1,SEA\n")
    assert teams[0]["team"] == "LAR"


def test_parse_combine():
    text = (
        "season,draft_year,draft_team,draft_round,draft_ovr,pfr_id,cfb_id,player_name,pos,school,ht,wt,"
        "forty,bench,vertical,broad_jump,cone,shuttle\n"
        "2023,2023,Detroit Lions,1,12,GibbJa00,jahmyr-gibbs-1,Jahmyr Gibbs,RB,Alabama,5-9,199,4.36,,33.5,120,,\n"
        "2001,,,,,,,No Id,WR,State,NA,NA,NA,NA,NA,NA,NA,NA\n"
    )
    gibbs, no_id = nflverse.parse_combine(text)
    assert gibbs["combine_id"] == "GibbJa00"
    assert gibbs["height_in"] == 69 and gibbs["forty"] == 4.36 and gibbs["broad_jump"] == 120
    assert gibbs["bench"] is None and gibbs["draft_pick"] == 12
    assert no_id["combine_id"] == "2001:No Id:WR:State"
    assert no_id["height_in"] is None and no_id["forty"] is None


def test_non_finite_numbers_are_treated_as_missing():
    rows = nflverse.parse_players("gsis_id,display_name,height,weight\n00-1,A,inf,nan\n")
    assert rows[0]["height"] is None and rows[0]["weight"] is None
