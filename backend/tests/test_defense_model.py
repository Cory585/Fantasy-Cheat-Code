import json

import numpy as np
import pytest

from app.defense import features as defense_features
from app.defense.features import (
    EXPECTED_SIGNS,
    FEATURES,
    DefenseFeatureBuilder,
    expected_pa_score,
    implied_points,
)
from app.defense.model import RidgeModel
from app.defense.scoring import dst_points, points_allowed_score
from app.defense.train import backtest_rows, played_rows, train
from app.services.special_teams import DST_LABELS
from tests.league_fixture import build_league


def test_dst_points():
    stats = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1, "def_tds": 1}
    assert dst_points(stats, 10) == 3 + 2 + 2 + 6 + 4
    # Play-by-play sacks (team sacks included) replace the players' credited sacks when known.
    assert dst_points(stats, 10, sacks=4) == 4 + 2 + 2 + 6 + 4


def test_targets_count_sacks_from_play_by_play(tmp_path):
    conn = build_league(tmp_path / "league.db")
    # One of BUF's week-4 sacks wasn't credited to a player: the stat line says 2, play-by-play 3.
    conn.execute("UPDATE team_game_offense SET sacks = 3 WHERE opponent = 'BUF' AND season = 2025 AND week = 4")
    stats = json.loads(conn.execute("SELECT stats FROM team_week_stats WHERE team = 'BUF' AND season = 2025 "
                                    "AND week = 4").fetchone()[0])
    stats["def_sacks"] = 2
    conn.execute("UPDATE team_week_stats SET stats = ? WHERE team = 'BUF' AND season = 2025 AND week = 4",
                 (json.dumps(stats),))
    conn.commit()
    row = rows_by_key(DefenseFeatureBuilder(conn))[(2025, 4, "BUF")]
    assert row.target == dst_points(stats, 22, sacks=3) == dst_points(stats, 22) + 1


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


def test_play_calling_prior_uses_the_coach_of_that_week(tmp_path):
    """MIA fires its coach after week 3 of 2025: the early weeks keep the first coach's prior."""
    conn = build_league(tmp_path / "league.db")
    coach = {(2024, "BUF"): "Pass Coach", (2024, "NE"): "Run Coach", (2024, "MIA"): "Old", (2024, "NYJ"): "Jets"}
    for (season, team), name in coach.items():
        for side in ("home", "away"):
            conn.execute(f"UPDATE games SET {side}_coach = ? WHERE season = ? AND {side}_team = ?",  # noqa: S608
                         (name, season, team))
    for side in ("home", "away"):
        conn.execute(f"UPDATE games SET {side}_coach = CASE WHEN week <= 3 THEN 'Pass Coach' "  # noqa: S608
                     f"ELSE 'Run Coach' END WHERE season = 2025 AND {side}_team = 'MIA'")
    # BUF passed far more than expected in 2024, NE far less.
    conn.execute("UPDATE team_game_offense SET pass_oe_sum = 6.0 WHERE season = 2024 AND team = 'BUF'")
    conn.execute("UPDATE team_game_offense SET pass_oe_sum = -6.0 WHERE season = 2024 AND team = 'NE'")
    conn.commit()
    builder = DefenseFeatureBuilder(conn)
    assert builder.coach(2025, "MIA", 2) == "Pass Coach" and builder.coach(2025, "MIA", 5) == "Run Coach"
    assert builder.coach(2025, "MIA", 9) == "Run Coach"  # a week with no game: the latest coach
    rows = rows_by_key(builder)
    # Week 1: MIA at BUF, so BUF's defense faces MIA with nothing from 2025 to go on yet.
    assert rows[(2025, 1, "BUF")].context["opp_coach"] == "Pass Coach"
    assert rows[(2025, 1, "BUF")].features["opp_proe"] > 0
    # After the firing the prior is the new coach's (still blended with MIA's neutral 2025 weeks).
    assert rows[(2025, 4, "BUF")].context["opp_coach"] == "Run Coach"
    assert rows[(2025, 4, "BUF")].features["opp_proe"] < 0


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


def _remove_lines(conn, from_week: int):
    conn.execute("UPDATE games SET spread_line = NULL, total_line = NULL WHERE season = 2025 AND week >= ?",
                 (from_week,))
    conn.commit()


def test_games_without_a_line_get_an_estimate_from_both_teams(tmp_path):
    conn = build_league(tmp_path / "league.db", unplayed_from=5)
    _remove_lines(conn, 5)
    rows = rows_by_key(DefenseFeatureBuilder(conn))
    # Week 5: BUF at NE. BUF's defense faces a weak offense, NE's faces the best one.
    buf, ne = rows[(2025, 5, "BUF")], rows[(2025, 5, "NE")]
    assert buf.context["line_estimated"] and not rows[(2025, 4, "BUF")].context["line_estimated"]
    assert buf.context["opp_implied_points"] is None
    assert buf.features["opp_implied_points"] < ne.features["opp_implied_points"]
    assert buf.features["def_favored_by"] > 0 > ne.features["def_favored_by"]
    assert buf.features["def_favored_by"] == pytest.approx(-ne.features["def_favored_by"])


def test_line_estimate_is_fit_to_the_loaded_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(defense_features, "MIN_LINE_FIT_ROWS", 10)
    builder = DefenseFeatureBuilder(build_league(tmp_path / "league.db"))
    assert builder.line_coefs() != defense_features.DEFAULT_LINE_COEFS
    # The fixture's lines follow scoring exactly, so the fit reproduces them.
    for g in builder.games:
        if g["season"] == 2025 and g["week"] >= 3:
            expected = implied_points(True, g["spread"], g["total"])
            estimate = builder.estimated_points(2025, g["week"], g["home"], g["away"], True)
            assert estimate == pytest.approx(expected, abs=0.5)


def test_backtest_rows_fit_the_line_estimate_on_earlier_seasons_only(tmp_path, monkeypatch):
    monkeypatch.setattr(defense_features, "MIN_LINE_FIT_ROWS", 10)
    conn = build_league(tmp_path / "league.db", seasons=(2023, 2024, 2025))
    _remove_lines(conn, 6)  # week 6 of 2025 is played but has no line, so it uses the estimate

    def week6(rows):
        return {r.defense: r.features for r in rows if r.season == 2025 and r.week == 6}

    builder = DefenseFeatureBuilder(conn)
    before_full = week6(played_rows(builder, [2024, 2025]))
    before_split = week6(backtest_rows(builder, [2024, 2025], 2025))
    assert builder.line_fit_seasons is None  # back to fitting on everything afterwards
    # Moving 2025's own lines moves the full-history estimate, but not the backtest's.
    conn.execute("UPDATE games SET total_line = total_line + 20 WHERE season = 2025 AND week < 6")
    conn.commit()
    builder = DefenseFeatureBuilder(conn)
    assert week6(played_rows(builder, [2024, 2025])) != before_full
    assert week6(backtest_rows(builder, [2024, 2025], 2025)) == before_split


def test_training_saves_the_line_estimate_it_used(tmp_path):
    conn = build_league(tmp_path / "league.db", seasons=(2023, 2024, 2025))
    model, backtests = train(conn, [2024, 2025])
    assert model.metadata["line_coefs"] == list(DefenseFeatureBuilder(conn).line_coefs())
    assert [bt["test_season"] for bt in backtests] == [2025]


def test_line_estimate_falls_back_to_defaults_with_few_games(builder):
    assert builder.line_coefs() == defense_features.DEFAULT_LINE_COEFS
