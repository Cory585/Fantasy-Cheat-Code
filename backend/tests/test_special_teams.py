import numpy as np
import pytest

from app.defense.features import EXPECTED_SIGNS as DST_SIGNS
from app.defense.features import FEATURES as DST_FEATURES
from app.defense.features import DefenseFeatureBuilder
from app.defense.model import RidgeModel
from app.defense.train import to_arrays
from app.kicker.features import EXPECTED_SIGNS as K_SIGNS
from app.kicker.features import FEATURES as K_FEATURES
from app.kicker.features import KickerFeatureBuilder
from app.models.player import Position
from app.models.start_sit import Effect, GameStatus
from app.models.trade import ScoringFormat
from app.services.matchups import load_matchups
from app.services.special_teams import ModelEstimate, TeamModels, load_team_models
from app.services.start_sit import MODEL_CAP
from tests.league_fixture import build_league
from tests.test_start_sit import WEEK, advisor, matchups, player


@pytest.fixture
def league(tmp_path):
    """A league with weeks 5-6 of 2025 still to play, and models trained on the played games."""
    conn = build_league(tmp_path / "league.db", seasons=(2024, 2025), unplayed_from=5)
    builder = KickerFeatureBuilder(conn)
    for rows, features, signs, name in (
        (builder.dst_rows([2024, 2025]), DST_FEATURES, DST_SIGNS, "dst.json"),
        (builder.rows([2024, 2025]), K_FEATURES, K_SIGNS, "k.json"),
    ):
        X, y, _ = to_arrays([r for r in rows if r.target is not None], features)
        RidgeModel.fit(X, y, features, 10.0, signs=signs).save(tmp_path / name)
    return conn, tmp_path / "dst.json", tmp_path / "k.json"


def test_every_team_gets_an_estimate_for_every_game(league):
    conn, dst_path, k_path = league
    models = load_team_models(conn, 2025, dst_path, k_path)
    for by_team in (models.dst, models.kicker):
        assert set(by_team) == {"BUF", "MIA", "NE", "NYJ"}
        assert all(set(weeks) == set(range(1, 7)) for weeks in by_team.values())
    # Actual DST points only for played games.
    assert set(models.dst_actual["BUF"]) == {1, 2, 3, 4}
    estimate = models.kicker["BUF"][5]
    assert np.isfinite(estimate.points)
    assert all(abs(value) >= 0.15 for _, value in estimate.factors)
    assert len(estimate.factors) <= 3


def test_stronger_offense_gets_the_higher_kicker_estimate(league):
    conn, dst_path, k_path = league
    models = load_team_models(conn, 2025, dst_path, k_path)
    assert models.kicker["BUF"][5].points > models.kicker["NYJ"][5].points


def test_kicker_games_without_a_line_say_the_points_are_projected(league):
    conn, dst_path, k_path = league
    conn.execute("UPDATE games SET spread_line = NULL, total_line = NULL WHERE season = 2025 AND week >= 5")
    conn.commit()
    models = load_team_models(conn, 2025, dst_path, k_path)
    labels = {label for label, _ in models.kicker["BUF"][5].factors}
    assert labels & {"team's projected points", "projected point spread"}
    assert not labels & {"team's Vegas total", "point spread"}


def test_games_without_a_line_use_the_line_estimate_each_model_was_trained_with(league, tmp_path):
    conn, _, _ = league
    conn.execute("UPDATE games SET spread_line = NULL, total_line = NULL WHERE season = 2025 AND week >= 5")
    conn.commit()
    # Each model predicts exactly its implied-points feature, and was trained with a line
    # estimate that's a constant (DST: 30 points, kicker: 20).
    for feature, coefs, name in (("opp_implied_points", [30.0, 0, 0, 0], "dst.json"),
                                 ("team_implied_points", [20.0, 0, 0, 0], "k.json")):
        RidgeModel([feature], np.array([0.0]), np.array([1.0]), np.array([1.0]), 0.0, 1.0,
                   {"line_coefs": coefs}).save(tmp_path / name)
    models = load_team_models(conn, 2025, tmp_path / "dst.json", tmp_path / "k.json")
    assert models.dst["BUF"][5].points == 30.0
    assert models.kicker["BUF"][5].points == 20.0
    # A model saved without them fits the estimate on the loaded games, as before.
    RidgeModel(["opp_implied_points"], np.array([0.0]), np.array([1.0]), np.array([1.0]), 0.0, 1.0,
               {}).save(tmp_path / "old.json")
    builder = KickerFeatureBuilder(conn, first_season=2024)
    expected = builder.estimated_points(2025, 5, "NE", "BUF", True)
    assert load_team_models(conn, 2025, tmp_path / "old.json", None).dst["BUF"][5].points == round(expected, 2)


def test_without_saved_models_only_actual_dst_points_are_loaded(league, tmp_path):
    conn, _, _ = league
    models = load_team_models(conn, 2025, tmp_path / "missing.json", None)
    assert models.dst == {} and models.kicker == {}
    assert models.dst_actual["NYJ"]


def test_a_model_with_unknown_features_still_predicts(league, tmp_path):
    conn, _, _ = league
    model = RidgeModel(["exp_pa_score", "retired_feature"], np.array([0.0, 1.0]), np.array([1.0, 1.0]),
                       np.array([1.0, 5.0]), 6.0, 1.0, {})
    model.save(tmp_path / "old.json")
    models = load_team_models(conn, 2025, tmp_path / "old.json", None)
    row = next(r for r in DefenseFeatureBuilder(conn).rows([2025], include_unplayed=True)
               if r.defense == "BUF" and r.week == 5)
    # The unknown feature counts as average (z = 0), so only exp_pa_score contributes.
    assert models.dst["BUF"][5].points == pytest.approx(6.0 + row.features["exp_pa_score"], abs=0.01)


def test_first_season_filter_gives_the_same_rows(tmp_path):
    conn = build_league(tmp_path / "league.db", seasons=(2023, 2024, 2025))
    full = KickerFeatureBuilder(conn).dst_rows([2025])
    recent = KickerFeatureBuilder(conn, first_season=2024).dst_rows([2025])
    assert [r.features for r in full] == [r.features for r in recent]


def test_load_matchups_includes_the_models(league):
    conn, dst_path, k_path = league
    data = load_matchups(conn, 2025, ScoringFormat.PPR, dst_model_path=dst_path, kicker_model_path=k_path)
    kicker = player("k", position=Position.K, team="BUF")
    assert data.model_estimate(kicker, 5) == data.team_models.kicker["BUF"][5]
    assert data.model_estimate(player("wr", team="BUF"), 5) is None
    assert data.history_for(player("d", position=Position.DST, team="BUF")) == data.team_models.dst_actual["BUF"]


# ---------- start/sit ----------

def with_models(**team_models):
    return matchups(team_models=TeamModels(**team_models))


def test_kicker_projection_blends_in_the_model():
    estimate = ModelEstimate(10.0, (("team's Vegas total", 1.2), ("wind", -0.4)))
    data = with_models(kicker={"AAA": {WEEK: estimate}})
    pw = advisor(data, model_weight=0.5).evaluate(player("k", position=Position.K, week_points=8.0), WEEK)
    assert pw.if_active == pytest.approx(9.0)
    reason = next(r for r in pw.reasons if r.kind == "model")
    assert reason.effect == Effect.PLUS
    assert "10.0 points for the AAA kicker" in reason.text
    assert "Helps: team's Vegas total (+1.2)" in reason.text and "Hurts: wind (-0.4)" in reason.text
    assert not any(r.kind == "form" for r in pw.reasons)  # the model replaces form for kickers


def test_model_estimate_is_capped():
    data = with_models(dst={"AAA": {WEEK: ModelEstimate(40.0)}})
    pw = advisor(data, model_weight=1.0).evaluate(player("d", position=Position.DST, week_points=6.0), WEEK)
    assert pw.if_active == pytest.approx(6.0 * (1 + MODEL_CAP))


def test_model_is_skipped_for_players_ruled_out():
    data = with_models(kicker={"AAA": {WEEK: ModelEstimate(12.0)}})
    pw = advisor(data).evaluate(player("k", position=Position.K, week_points=8.0, injury="O"), WEEK)
    assert pw.if_active == 8.0 and pw.projection == 0
    assert not any(r.kind == "model" for r in pw.reasons)


def test_played_dst_week_shows_the_actual_score():
    data = with_models(dst_actual={"AAA": {9: 14.0}})
    pw = advisor(data).evaluate(player("d", position=Position.DST, week_points=6.0), 9)
    assert pw.status == GameStatus.PLAYED and pw.actual_points == 14.0
