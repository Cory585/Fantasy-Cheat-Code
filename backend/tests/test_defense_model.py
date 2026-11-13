import json

import numpy as np
import pytest

from app.defense.features import (
    EXPECTED_SIGNS,
    FEATURES,
    DefenseFeatureBuilder,
    expected_pa_score,
)
from app.defense.model import RidgeModel
from app.defense.scoring import dst_points, points_allowed_score
from app.services.special_teams import DST_LABELS
from tests.league_fixture import build_league


def test_dst_points():
    stats = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1, "def_tds": 1}
    assert dst_points(stats, 10) == 3 + 2 + 2 + 6 + 4


def test_expected_pa_score_is_smooth_and_matches_tiers_at_the_extremes():
    assert expected_pa_score(-50) == pytest.approx(10, abs=1e-3)
    assert expected_pa_score(80) == pytest.approx(points_allowed_score(80), abs=1e-3)
    scores = [expected_pa_score(x) for x in range(0, 45)]
    assert all(a >= b for a, b in zip(scores, scores[1:]))  # more points allowed never helps
    # No jump at a tier boundary, unlike the step function itself.
    assert abs(expected_pa_score(13.4) - expected_pa_score(13.6)) < 0.1


def test_every_feature_has_an_expected_sign_and_a_label():
    assert set(EXPECTED_SIGNS) == set(FEATURES)
    assert set(DST_LABELS) == set(FEATURES)


@pytest.fixture
def builder(tmp_path):
    return DefenseFeatureBuilder(build_league(tmp_path / "league.db"))


def rows_by_key(builder, seasons=(2025,)):
    return {(r.season, r.week, r.defense): r for r in builder.rows(list(seasons), include_unplayed=True)}


def test_every_feature_is_computed(builder):
    for row in builder.rows([2025]):
        assert set(row.features) == set(FEATURES)
        assert all(np.isfinite(v) for v in row.features.values())


def test_matchup_features_favor_the_better_side(builder):
    rows = rows_by_key(builder)
    # Week 4 2025: MIA at BUF. BUF's defense faces a weaker offense and is favored.
    buf, mia = rows[(2025, 4, "BUF")], rows[(2025, 4, "MIA")]
    assert buf.features["def_favored_by"] == 3.0 and mia.features["def_favored_by"] == -3.0
    assert buf.features["exp_pa_score"] > mia.features["exp_pa_score"]
    assert buf.features["opp_sack_rate"] > mia.features["opp_sack_rate"]  # MIA allows more sacks than BUF


def test_features_only_use_earlier_weeks(tmp_path):
    """Changing a game's own stats changes its target, never its features."""
    conn = build_league(tmp_path / "league.db")
    before = rows_by_key(DefenseFeatureBuilder(conn))
    stats = json.loads(conn.execute("SELECT stats FROM team_week_stats WHERE team = 'BUF' AND season = 2025 "
                                    "AND week = 4").fetchone()[0])
    stats["def_sacks"] = 9
    stats["def_qb_hits"] = 15
    conn.execute("UPDATE team_week_stats SET stats = ? WHERE team = 'BUF' AND season = 2025 AND week = 4",
                 (json.dumps(stats),))
    conn.execute("UPDATE team_game_offense SET sacks = 9 WHERE opponent = 'BUF' AND season = 2025 AND week = 4")
    conn.commit()
    after = rows_by_key(DefenseFeatureBuilder(conn))
    key = (2025, 4, "BUF")
    assert after[key].features == before[key].features
    assert after[key].target > before[key].target
    assert after[(2025, 5, "BUF")].features != before[(2025, 5, "BUF")].features


def test_unplayed_games_have_features_but_no_target(tmp_path):
    builder = DefenseFeatureBuilder(build_league(tmp_path / "league.db", unplayed_from=5))
    rows = rows_by_key(builder)
    assert rows[(2025, 5, "BUF")].target is None
    assert rows[(2025, 4, "BUF")].target is not None
    assert set(rows[(2025, 5, "BUF")].features) == set(FEATURES)


def test_sign_constrained_fit_drops_wrong_way_features():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 2))
    y = 2 * x[:, 0] + rng.normal(scale=0.1, size=400)
    model = RidgeModel.fit(np.column_stack([x[:, 0], -x[:, 0] + 0.01 * x[:, 1]]), y, ["good", "flipped"], 1.0,
                           signs={"good": +1, "flipped": +1})
    assert model.coefs[0] > 0 and model.coefs[1] == 0
    assert "flipped" in model.metadata["dropped_for_sign"]


def test_weighted_fit_matches_unweighted_with_equal_weights_and_follows_heavy_rows():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 2))
    y = 2 * X[:, 0] + rng.normal(size=200)
    plain = RidgeModel.fit(X, y, ["a", "b"], 1.0)
    equal = RidgeModel.fit(X, y, ["a", "b"], 1.0, weights=np.full(200, 3.0))
    assert np.allclose(plain.coefs, equal.coefs) and plain.intercept == pytest.approx(equal.intercept)
    # Rows weighted up pull the intercept toward their average.
    w = np.where(y > 0, 10.0, 1.0)
    assert RidgeModel.fit(X, y, ["a", "b"], 1.0, weights=w).intercept > plain.intercept
