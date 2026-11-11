import json

import pytest

from app.data import db
from app.models.player import Player, Position
from app.models.start_sit import Confidence, GameStatus, LineupSlot
from app.models.trade import ScoringFormat
from app.services.matchups import Game, MatchupData, load_matchups
from app.services.start_sit import FORM_CAP, StartSitAdvisor, StartSitError, check_slot

WEEK = 10


def player(pid, position=Position.WR, team="AAA", week_points=None, espn_id=None, injury=None, bye=None, ros=100.0):
    weekly = {WEEK: {"sleeper": week_points, "espn": week_points}} if week_points is not None else {}
    return Player(id=pid, name=f"Player {pid}", position=position, team=team, ros_projection=ros,
                  weekly_projections=weekly, espn_id=espn_id, injury_status=injury, bye_week=bye)


def matchups(**kw):
    schedule = {
        "AAA": {WEEK: Game(WEEK, "SOFT", True, False, 27.0, 3.0), 9: Game(9, "HARD", False, True)},
        "BBB": {WEEK: Game(WEEK, "HARD", False, False, 18.0, -3.0), 9: Game(9, "SOFT", True, True)},
        "SOFT": {WEEK: Game(WEEK, "AAA", False, False, 24.0, -3.0)},
        "HARD": {WEEK: Game(WEEK, "BBB", True, False, 21.0, 3.0)},
        "BYE": {9: Game(9, "AAA", True, True)},
    }
    factors = {pos: {"SOFT": 1.3, "HARD": 0.7, "AAA": 1.0, "BBB": 1.0} for pos in Position}
    return MatchupData(season=2026, schedule=schedule, defense_factors=factors, **kw)


def advisor(data=None, **kw):
    return StartSitAdvisor(data if data is not None else matchups(), 2026, (9, 17), simulations=2000, **kw)


def test_higher_projection_starts_and_matchup_is_explained():
    a, b = player("a", week_points=15.0), player("b", team="BBB", week_points=10.0)
    result = advisor().compare([b, a], week=WEEK)
    assert result.recommendation.start == ["a"] and result.recommendation.sit == ["b"]
    assert [pw.player.id for pw in result.players] == ["a", "b"]
    assert result.players[0].recommended and not result.players[1].recommended
    assert result.slot == LineupSlot.WR
    a_week = result.players[0]
    assert a_week.matchup.opponent == "SOFT" and a_week.matchup.label == "easy"
    assert a_week.matchup.implied_rank == 1
    assert any(r.kind == "matchup" and r.effect == "plus" for r in a_week.reasons)
    assert "easier matchup" in result.recommendation.summary


def test_close_call_is_a_toss_up_and_lopsided_one_is_confident():
    close = advisor().compare([player("a", week_points=12.1), player("b", week_points=12.0)], week=WEEK)
    assert close.recommendation.confidence == Confidence.TOSS_UP
    assert 0.4 < close.recommendation.probability < 0.6
    assert "it's close" in close.recommendation.summary
    clear = advisor().compare([player("a", week_points=25.0), player("b", week_points=5.0)], week=WEEK)
    assert clear.recommendation.confidence == Confidence.CONFIDENT
    assert clear.recommendation.probability > 0.9


def test_results_are_deterministic():
    players = [player("a", week_points=12.0), player("b", week_points=11.0)]
    assert advisor().compare(players, week=WEEK) == advisor().compare(players, week=WEEK)


def test_bye_week_player_sits():
    result = advisor().compare([player("a", week_points=20.0, team="BYE"), player("b", week_points=5.0)], week=WEEK)
    bye = next(pw for pw in result.players if pw.player.id == "a")
    assert bye.status == GameStatus.BYE and bye.projection == 0
    assert result.recommendation.start == ["b"]
    assert any("bye" in w for w in result.warnings)


def test_bye_without_schedule_uses_player_bye_week():
    data = MatchupData()
    result = advisor(data).compare([player("a", week_points=20.0, bye=WEEK), player("b", week_points=5.0)], week=WEEK)
    assert result.players[1].status == GameStatus.BYE
    assert any("matchup data" in w for w in result.warnings)


def test_injury_scales_expected_points_but_not_range():
    healthy = advisor().evaluate(player("a", week_points=20.0), WEEK)
    hurt = advisor().evaluate(player("a", week_points=20.0, injury="Q"), WEEK)
    assert hurt.projection == pytest.approx(healthy.projection * 0.85, abs=0.01)
    assert hurt.ceiling == healthy.ceiling
    out = advisor().evaluate(player("a", week_points=20.0, injury="O"), WEEK)
    assert out.projection == 0 and out.play_probability == 0


def test_form_against_this_opponent_is_blended_and_capped():
    history = {"g1": {6: 30.0, 7: 30.0, 8: 30.0, 9: 30.0}}
    data = matchups(history=history, gsis_by_espn={1: "g1"})
    pw = advisor(data, form_weight=0.5).evaluate(player("a", week_points=10.0, espn_id=1), WEEK)
    assert pw.recent_ppg == 30 and pw.recent_games == 4
    # 30 ppg x 1.3 (soft opponent) is far above the projection, so form is capped at +25%.
    assert pw.if_active == pytest.approx(0.5 * 10 + 0.5 * 10 * (1 + FORM_CAP))
    assert any(r.kind == "form" and r.effect == "plus" for r in pw.reasons)


def test_spread_uses_player_history():
    steady = {"g1": {w: 15.0 for w in range(1, 10)}}
    boom_bust = {"g1": {w: (0.0 if w % 2 else 30.0) for w in range(1, 10)}}
    p = player("a", week_points=15.0, espn_id=1)
    calm = advisor(matchups(history=steady, gsis_by_espn={1: "g1"}), form_weight=0).evaluate(p, WEEK)
    wild = advisor(matchups(history=boom_bust, gsis_by_espn={1: "g1"}), form_weight=0).evaluate(p, WEEK)
    assert wild.ceiling - wild.floor > calm.ceiling - calm.floor
    assert calm.floor > 0


def test_finished_game_uses_actual_points():
    data = matchups(history={"g1": {9: 22.5}}, gsis_by_espn={1: "g1"})
    pw = advisor(data).evaluate(player("a", espn_id=1), 9)
    assert pw.status == GameStatus.PLAYED and pw.projection == 22.5 and pw.actual_points == 22.5


def test_missing_weekly_projections_fall_back_to_rest_of_season():
    pw = advisor(MatchupData()).evaluate(player("a", ros=90.0), 9)
    assert pw.projection == pytest.approx(90.0 / 9)  # weeks 9-17, no bye
    assert pw.reasons[0].kind == "estimate"


def test_current_week_is_first_with_unplayed_games():
    assert advisor().current_week == WEEK
    assert advisor(MatchupData()).current_week == 9
    assert advisor().compare([player("a", week_points=1.0), player("b", week_points=2.0)]).week == WEEK


def test_week_outside_range_is_rejected():
    with pytest.raises(StartSitError) as error:
        advisor().compare([player("a"), player("b")], week=20)
    assert error.value.code == "invalid_week"


def test_multiple_starts():
    players = [player(p, week_points=pts) for p, pts in [("a", 20.0), ("b", 15.0), ("c", 14.0), ("d", 3.0)]]
    result = advisor().compare(players, week=WEEK, starts=2)
    assert result.recommendation.start == ["a", "b"]
    assert sum(pw.start_probability for pw in result.players) == pytest.approx(2.0)
    with pytest.raises(StartSitError):
        advisor().compare(players, week=WEEK, starts=4)


def test_duplicate_players_are_rejected():
    with pytest.raises(StartSitError):
        advisor().compare([player("a"), player("a")], week=WEEK)


def test_slot_inference_and_eligibility():
    wr, rb, qb, k = player("wr"), player("rb", Position.RB), player("qb", Position.QB), player("k", Position.K)
    assert check_slot([wr, player("wr2")], None) == LineupSlot.WR
    assert check_slot([wr, rb], None) == LineupSlot.FLEX
    assert check_slot([wr, qb], None) == LineupSlot.SUPERFLEX
    assert check_slot([wr, rb], LineupSlot.SUPERFLEX) == LineupSlot.SUPERFLEX
    with pytest.raises(StartSitError):
        check_slot([wr, k], None)
    with pytest.raises(StartSitError):
        check_slot([wr, rb], LineupSlot.WR)


def test_load_matchups_from_database(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    base = {"season": 2026, "game_type": "REG", "gameday": None, "gametime": None, "roof": None, "surface": None,
            "temp": None, "wind": None, "div_game": None, "espn_id": None}
    db.upsert(conn, "games", [
        {**base, "game_id": "g9", "week": 9, "away_team": "AAA", "home_team": "BBB", "away_score": 10,
         "home_score": 20, "spread_line": None, "total_line": None},
        {**base, "game_id": "g10", "week": 10, "away_team": "BBB", "home_team": "AAA", "away_score": None,
         "home_score": None, "spread_line": 3.0, "total_line": 45.0},
    ])
    db.upsert(conn, "player_week_stats", [
        {"gsis_id": "g1", "season": 2026, "week": 9, "season_type": "REG", "name": "x", "position": "WR",
         "team": "AAA", "opponent": "BBB", "fantasy_points": 10.0, "fantasy_points_ppr": 14.0,
         "stats": json.dumps({})},
    ])
    data = load_matchups(conn, 2026, ScoringFormat.PPR)
    home = data.game("AAA", 10)
    assert home.home and home.implied_total == 24.0 and home.favored_by == 3.0
    assert data.game("BBB", 10).implied_total == 21.0 and data.game("BBB", 10).favored_by == -3.0
    assert data.game("AAA", 9).played
    assert data.history == {"g1": {9: 14.0}}
    assert data.implied_rank("AAA", 10) == (1, 2)


# ---------- Lineups ----------

from app.models.start_sit import DEFAULT_LINEUP  # noqa: E402

SLOTS = {LineupSlot(k): v for k, v in DEFAULT_LINEUP.items()}


def roster():
    return [
        player("qb1", Position.QB, week_points=22.0), player("qb2", Position.QB, week_points=18.0),
        player("rb1", Position.RB, week_points=16.0), player("rb2", Position.RB, week_points=12.0),
        player("rb3", Position.RB, week_points=11.5),
        player("wr1", week_points=17.0), player("wr2", week_points=13.0), player("wr3", week_points=9.0),
        player("te1", Position.TE, week_points=10.0), player("te2", Position.TE, week_points=4.0),
        player("k1", Position.K, week_points=8.0), player("dst1", Position.DST, week_points=7.0),
    ]


def starters(analysis):
    return [(s.slot.value, s.starter.player.id if s.starter else None) for s in analysis.lineup]


def test_best_lineup_fills_narrow_spots_first():
    result = advisor().lineup(roster(), SLOTS, week=WEEK)
    assert starters(result) == [("QB", "qb1"), ("RB", "rb1"), ("RB", "rb2"), ("WR", "wr1"), ("WR", "wr2"),
                                ("TE", "te1"), ("FLEX", "rb3"), ("K", "k1"), ("DST", "dst1")]
    assert [pw.player.id for pw in result.bench] == ["qb2", "wr3", "te2"]
    assert result.projected_total == pytest.approx(sum(s.starter.projection for s in result.lineup))
    assert all(s.starter.recommended for s in result.lineup)
    assert not any(pw.recommended for pw in result.bench)


def test_each_spot_names_the_best_alternative_and_close_calls():
    result = advisor().lineup(roster(), SLOTS, week=WEEK)
    flex = next(s for s in result.lineup if s.slot == LineupSlot.FLEX)
    assert flex.alternative.player.id == "wr3"
    qb = result.lineup[0]
    assert qb.alternative.player.id == "qb2" and 0.5 < qb.probability < 1
    kicker = next(s for s in result.lineup if s.slot == LineupSlot.K)
    assert kicker.alternative is None and kicker.probability is None
    assert result.close_calls == sorted(result.close_calls, key=lambda i: result.lineup[i].probability)
    assert all(result.lineup[i].probability < 0.7 for i in result.close_calls)


def test_superflex_takes_the_second_quarterback():
    slots = {**SLOTS, LineupSlot.SUPERFLEX: 1}
    result = advisor().lineup(roster(), slots, week=WEEK)
    assert ("SUPERFLEX", "qb2") in starters(result)


def test_bye_week_starter_is_replaced():
    team = roster()
    team[0] = player("qb1", Position.QB, week_points=22.0, team="BYE")
    result = advisor().lineup(team, SLOTS, week=WEEK)
    assert result.lineup[0].starter.player.id == "qb2"


def test_missing_positions_leave_empty_spots_with_a_warning():
    result = advisor().lineup([player("wr1", week_points=10.0)], SLOTS, week=WEEK)
    assert ("WR", "wr1") in starters(result) and ("QB", None) in starters(result)
    assert any("QB spot" in w for w in result.warnings)


def test_lineup_needs_a_spot():
    with pytest.raises(StartSitError):
        advisor().lineup(roster(), {LineupSlot.QB: 0}, week=WEEK)


def test_current_week_skips_unscored_games_whose_date_has_passed():
    from datetime import date

    schedule = {
        "AAA": {9: Game(9, "BBB", True, False, gameday="2026-11-09"), 10: Game(10, "CCC", True, False, gameday="2026-11-15")},
    }
    data = MatchupData(season=2026, schedule=schedule)
    on = lambda day: StartSitAdvisor(data, 2026, (9, 17), today=lambda: date.fromisoformat(day)).current_week  # noqa: E731
    assert on("2026-11-09") == 9  # Monday night game still to come
    assert on("2026-11-11") == 10  # collected before it finished; the week is over
    assert on("2027-01-30") == 17  # season over: the last week
