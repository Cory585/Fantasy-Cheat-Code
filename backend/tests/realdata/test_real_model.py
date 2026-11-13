"""How the DST model does on real NFL games it wasn't trained on.

Each season from 2020 is predicted by a model fit only on earlier seasons (2019 on; 2018 is
the prior), exactly as `python -m app.defense.train` backtests it. The thresholds are set
below what the model actually achieves, with room for normal season-to-season noise, so a
failure means it got meaningfully worse, not that one week went badly.

What the backtests show (MAE / weekly rank correlation, model vs Vegas only):
    2020  4.08 / 0.32  vs 4.03 / 0.32   one training season, and the no-fans COVID year
    2021  4.36 / 0.26  vs 4.36 / 0.27
    2022  3.87 / 0.21  vs 3.88 / 0.20
    2023  4.27 / 0.28  vs 4.28 / 0.27
    2024  3.99 / 0.35  vs 4.07 / 0.33
    2025  4.10 / 0.33  vs 4.18 / 0.34
"""

import numpy as np
import pytest

from app.defense import train
from app.defense.features import EXPECTED_SIGNS, FEATURES, implied_points
from app.defense.model import RidgeModel
from tests.realdata.conftest import COMPLETE_SEASONS

pytestmark = pytest.mark.realdata

TEST_SEASONS = [s for s in COMPLETE_SEASONS if s >= 2020]
# From 2021 the model has at least two seasons to learn from.
SEASONS_WITH_HISTORY = [s for s in TEST_SEASONS if s >= 2021]


@pytest.fixture(scope="module")
def backtests(dst_rows):
    """season -> (train.backtest result, test rows, predictions, actual points)."""
    out = {}
    for season in TEST_SEASONS:
        result = train.backtest(dst_rows, season)
        history = [r for r in dst_rows if r.season < season]
        test = [r for r in dst_rows if r.season == season]
        X, y, _ = train.to_arrays(history)
        Xt, yt, _ = train.to_arrays(test)
        model = RidgeModel.fit(X, y, FEATURES, result["alpha"], signs=EXPECTED_SIGNS)
        out[season] = (result, test, model.predict(Xt), yt)
    return out


# ---------- accuracy against baselines ----------

@pytest.mark.parametrize("season", TEST_SEASONS)
def test_beats_naive_baselines(backtests, season):
    result = backtests[season][0]
    model, base = result["model"], result["baselines"]
    assert model["mae"] < base["league_average"]["mae"] - 0.05
    assert model["mae"] < base["defense_season_average"]["mae"] - 0.1
    # Ordering defenses is what start/sit needs: well above a defense's own season average.
    assert model["weekly_rank_corr"] >= 0.2
    assert model["weekly_rank_corr"] > base["defense_season_average"]["weekly_rank_corr"] + 0.05


@pytest.mark.parametrize("season", SEASONS_WITH_HISTORY)
def test_at_least_matches_vegas_with_two_seasons_of_history(backtests, season):
    result = backtests[season][0]
    vegas = result["baselines"]["vegas_only"]
    assert result["model"]["mae"] <= vegas["mae"] + 0.01
    assert result["model"]["weekly_rank_corr"] >= vegas["weekly_rank_corr"] - 0.02


def test_beats_vegas_over_all_seasons_with_history(backtests):
    model = np.mean([backtests[s][0]["model"]["mae"] for s in SEASONS_WITH_HISTORY])
    vegas = np.mean([backtests[s][0]["baselines"]["vegas_only"]["mae"] for s in SEASONS_WITH_HISTORY])
    assert model < vegas - 0.02


# ---------- calibration ----------

@pytest.mark.parametrize("season", TEST_SEASONS)
def test_projections_are_unbiased_and_in_a_sensible_range(backtests, season):
    _, _, pred, actual = backtests[season]
    # 2020 runs about 0.6 high: one training season, and no crowds changed scoring.
    assert abs(pred.mean() - actual.mean()) < 0.75
    assert 0 <= pred.min() and pred.max() <= 15  # real DST games range from about -4 to 30+


def test_higher_projections_mean_more_real_points(backtests):
    """Pooled over 2021-2025, each fifth of projections scores more than the one below it."""
    pred = np.concatenate([backtests[s][2] for s in SEASONS_WITH_HISTORY])
    actual = np.concatenate([backtests[s][3] for s in SEASONS_WITH_HISTORY])
    order = np.argsort(pred)
    fifths = [actual[chunk].mean() for chunk in np.array_split(order, 5)]
    assert fifths == sorted(fifths)
    assert fifths[-1] - fifths[0] > 3
    assert abs(pred.mean() - actual.mean()) < 0.3


@pytest.mark.parametrize("season", TEST_SEASONS)
def test_weekly_top_five_outscore_the_bottom_five(backtests, season):
    _, test, pred, actual = backtests[season]
    top, bottom = [], []
    for week in sorted({r.week for r in test}):
        ranked = sorted((i for i, r in enumerate(test) if r.week == week), key=lambda i: -pred[i])
        top += [actual[i] for i in ranked[:5]]
        bottom += [actual[i] for i in ranked[-5:]]
    assert np.mean(top) - np.mean(bottom) > 2.5


# ---------- consistency on real matchups ----------

@pytest.mark.parametrize("season", TEST_SEASONS)
def test_easiest_matchup_projects_above_the_hardest_every_week(backtests, season):
    """Whatever else differs, the defense facing the week's lowest Vegas total should project
    above the one facing the highest."""
    _, test, pred, _ = backtests[season]
    for week in sorted({r.week for r in test}):
        rows = [i for i, r in enumerate(test) if r.week == week]
        easiest = min(rows, key=lambda i: test[i].features["opp_implied_points"])
        hardest = max(rows, key=lambda i: test[i].features["opp_implied_points"])
        assert pred[easiest] > pred[hardest], f"{season} week {week}"


def test_dolphins_defense_projected_above_denvers_in_the_70_20_game(backtests):
    _, test, pred, actual = backtests[2023]
    index = {(r.week, r.defense): i for i, r in enumerate(test)}
    mia, den = index[(3, "MIA")], index[(3, "DEN")]
    assert pred[mia] > pred[den] + 2
    assert actual[mia] > actual[den]


def test_final_model_leans_on_the_vegas_line(dst_rows):
    X, y, _ = train.to_arrays(dst_rows)
    model = RidgeModel.fit(X, y, FEATURES, train.choose_alpha(dst_rows), signs=EXPECTED_SIGNS)
    coefs = dict(zip(FEATURES, model.coefs))
    assert all(EXPECTED_SIGNS[f] * c >= 0 for f, c in coefs.items())
    assert coefs["opp_implied_points"] < -0.2 and coefs["exp_pa_score"] > 0.2
    # The two Vegas features are the biggest effects.
    biggest = sorted(coefs, key=lambda f: -abs(coefs[f]))[:3]
    assert {"opp_implied_points", "exp_pa_score"} <= set(biggest)


# ---------- the line estimate for games without one ----------

def test_estimated_lines_track_real_vegas_lines(dst_builder):
    """For 2025 games that had lines, the estimate the model uses for games without one should be
    close to Vegas, and much closer than the raw points-per-game it replaced."""
    estimates, lines, raw = [], [], []
    for g in dst_builder.games:
        if g["season"] != 2025 or g["spread"] is None or g["week"] < 3:
            continue
        for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            lines.append(implied_points(home, g["spread"], g["total"]))
            estimates.append(dst_builder.estimated_points(2025, g["week"], team, opp, home))
            raw.append(dst_builder._scoring(2025, team, g["week"])[0])
    lines, estimates, raw = map(np.array, (lines, estimates, raw))
    assert np.abs(estimates - lines).mean() < 2.2
    assert np.corrcoef(estimates, lines)[0, 1] > 0.6
    assert np.abs(estimates - lines).mean() < np.abs(raw - lines).mean() - 0.3


# ---------- no peeking at the game being predicted ----------

def test_features_ignore_the_game_being_predicted(real_db, need):
    """Rewriting a real game's box score changes the following week's features, never its own."""
    from app.defense.features import DefenseFeatureBuilder

    need(2024, 2025)
    builder = DefenseFeatureBuilder(real_db, first_season=2024)
    before = {(r.week, r.defense): r.features for r in builder.rows([2025])}
    # Pittsburgh's week-5 defense gets 9 sacks, 15 QB hits, and 4 interceptions on paper.
    week = next(w for w in sorted(builder.defense[(2025, "PIT")]) if w >= 5)
    builder.defense[(2025, "PIT")][week] = {**builder.defense[(2025, "PIT")][week], "def_sacks": 9,
                                           "def_qb_hits": 15, "def_interceptions": 4}
    after = {(r.week, r.defense): r.features for r in builder.rows([2025])}
    assert after[(week, "PIT")] == before[(week, "PIT")]
    next_week = min(w for w in builder.defense[(2025, "PIT")] if w > week)
    assert after[(next_week, "PIT")] != before[(next_week, "PIT")]
