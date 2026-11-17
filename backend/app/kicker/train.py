"""Train and evaluate the kicker model.

Usage (from backend/, after `python -m app.data.collect --seasons 2022-2026`):
    python -m app.kicker.train

Same method as the DST model (`app.defense.train`): fit on earlier seasons, score on a later
one against simple baselines, then fit on every played game and save to
`backend/data/kicker_model.json`. Two differences, because kicking keeps changing (more 50+
yard tries, and league kicker scoring up about 0.15 points per game each season since 2019):

- Seasons are weighted by recency (SEASON_HALF_LIFE).
- With at least MIN_TREND_SEASONS seasons, the intercept is the league average projected
  from its trend across seasons instead of the (lagging) training average.

Training uses every season in the database after the first (which is only the prior), so
collecting older seasons (`--seasons 2017-2026`) gives it more history.
"""

import argparse
import logging
import sqlite3
from datetime import datetime, timezone

import numpy as np

from app.config import DATA_DIR, settings
from app.defense.model import RidgeModel, evaluate
from app.defense.train import backtest_rows, choose_alpha, first_season_with_prior, played_rows, to_arrays
from app.kicker.features import EXPECTED_SIGNS, FEATURES, KickerFeatureBuilder

log = logging.getLogger(__name__)

MODEL_PATH = DATA_DIR / "kicker_model.json"
SEASON_HALF_LIFE = 2.0  # a season counts half as much as the one two seasons after it
MIN_TREND_SEASONS = 4  # with fewer seasons than this the trend is mostly noise


def season_weights(rows, target_season: int) -> np.ndarray:
    """Recency weights for predicting `target_season` (the latest training season weighs 1)."""
    latest = min(target_season, max(r.season for r in rows))
    return np.array([0.5 ** ((latest - r.season) / SEASON_HALF_LIFE) for r in rows])


def trend_intercept(rows, target_season: int) -> float | None:
    """League kicker points per game in `target_season`, from a recency-weighted linear trend
    in the seasons' averages (None with too few seasons)."""
    seasons = sorted({r.season for r in rows})
    if len(seasons) < MIN_TREND_SEASONS:
        return None
    means = [np.mean([r.target for r in rows if r.season == s]) for s in seasons]
    weights = [0.5 ** ((seasons[-1] - s) / SEASON_HALF_LIFE) for s in seasons]
    slope, level = np.polyfit(np.array(seasons, dtype=float) - seasons[-1], means, 1, w=np.sqrt(weights))
    return float(level + slope * (target_season - seasons[-1]))


def fit(rows, target_season: int, alpha: float, metadata: dict | None = None) -> RidgeModel:
    """Recency-weighted, sign-constrained ridge, with the intercept from the league trend."""
    X, y, _ = to_arrays(rows, FEATURES)
    model = RidgeModel.fit(X, y, FEATURES, alpha, metadata, signs=EXPECTED_SIGNS,
                           weights=season_weights(rows, target_season))
    trend = trend_intercept(rows, target_season)
    model.metadata["training_average"] = round(model.intercept, 3)
    model.metadata["intercept_from_trend"] = trend is not None
    if trend is not None:
        model.intercept = trend
    return model


def backtest(rows, test_season: int) -> dict:
    train = [r for r in rows if r.season < test_season]
    test = [r for r in rows if r.season == test_season]
    X, y, _ = to_arrays(train, FEATURES)
    Xt, yt, wt = to_arrays(test, FEATURES)
    alpha = choose_alpha(train, FEATURES, EXPECTED_SIGNS)
    model = fit(train, test_season, alpha)

    def col(name, rows_=test):
        return np.array([r.features[name] for r in rows_])

    vegas_idx = [FEATURES.index("team_implied_points")]
    vegas = RidgeModel.fit(X[:, vegas_idx], y, ["team_implied_points"], alpha)
    league_allowed = col("opp_k_allowed_pg", train).mean()
    return {
        "test_season": test_season,
        "train_seasons": sorted({r.season for r in train}),
        "alpha": alpha,
        "model": evaluate(yt, model.predict(Xt), wt),
        "dropped_for_sign": model.metadata.get("dropped_for_sign", []),
        "intercept_from_trend": model.metadata["intercept_from_trend"],
        "baselines": {
            "league_average": evaluate(yt, np.full(len(yt), y.mean()), wt),
            "team_season_average": evaluate(yt, col("team_k_ppg"), wt),
            # What the app did before this model: points per game times how much the opponent
            # allows to kickers relative to the league.
            "ppg_times_matchup": evaluate(yt, col("team_k_ppg") * col("opp_k_allowed_pg") / league_allowed, wt),
            "vegas_only": evaluate(yt, vegas.predict(Xt[:, vegas_idx]), wt),
        },
    }


def train(conn: sqlite3.Connection, seasons: list[int]) -> tuple[RidgeModel, list[dict]]:
    builder = KickerFeatureBuilder(conn)
    rows = played_rows(builder, seasons)
    if not rows:
        raise RuntimeError("No played games with data. Run `python -m app.data.collect --seasons 2022-2026` first.")
    played_seasons = sorted({r.season for r in rows})
    backtests = [backtest(backtest_rows(builder, seasons, s), s) for s in played_seasons[1:]]
    alpha = choose_alpha(rows, FEATURES, EXPECTED_SIGNS)
    model = fit(rows, played_seasons[-1], alpha, metadata={
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seasons": played_seasons,
        "games": len(rows),
        # The line estimate the training rows used, so predictions use the same one.
        "line_coefs": list(builder.line_coefs()),
        "backtests": backtests,
    })
    return model, backtests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first-season", type=int, default=None,
                        help="first season to train on (default: the second season in the database)")
    parser.add_argument("--last-season", type=int, default=datetime.now().year)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    conn = sqlite3.connect(settings.database_path)
    first = args.first_season or first_season_with_prior(conn)
    model, backtests = train(conn, list(range(first, args.last_season + 1)))
    model.save(MODEL_PATH)

    for bt in backtests:
        log.info("\nTest season %s (trained on %s, alpha %s)", bt["test_season"], bt["train_seasons"], bt["alpha"])
        log.info("  %-24s %6s %6s %7s %10s", "", "MAE", "RMSE", "R2", "rank corr")
        for name, m in [("model", bt["model"])] + list(bt["baselines"].items()):
            log.info("  %-24s %6.3f %6.3f %7.4f %10s", name, m["mae"], m["rmse"], m["r2"], m["weekly_rank_corr"])
    log.info("\nFinal model: %d games, seasons %s, alpha %s", model.metadata["games"], model.metadata["seasons"], model.alpha)
    log.info("Intercept %.2f points (%s; recency-weighted training average %.2f)", model.intercept,
             "from the league trend" if model.metadata["intercept_from_trend"] else "training average",
             model.metadata["training_average"])
    log.info("Dropped (wrong direction once correlated features are included): %s",
             ", ".join(model.metadata.get("dropped_for_sign", [])) or "none")
    log.info("Points per 1 SD change (largest first):")
    for name, coef in sorted(zip(model.features, model.coefs), key=lambda t: -abs(t[1])):
        if coef:
            log.info("  %-24s %+.3f", name, coef)
    log.info("\nSaved to %s", MODEL_PATH)


if __name__ == "__main__":
    main()
