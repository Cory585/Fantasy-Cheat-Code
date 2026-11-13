"""Train and evaluate the DST model.

Usage (from backend/, after `python -m app.data.collect --seasons 2022-2026`):
    python -m app.defense.train

Evaluation is out of sample by season: the model is fit on earlier seasons and scored on a
later one, compared against simple baselines. The final model is then fit on every played
game and saved to `backend/data/defense_model.json`.
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone

import numpy as np

from app.config import DATA_DIR, settings
from app.defense.features import EXPECTED_SIGNS, FEATURES, DefenseFeatureBuilder, FeatureRow
from app.defense.model import RidgeModel, evaluate

log = logging.getLogger(__name__)

MODEL_PATH = DATA_DIR / "defense_model.json"
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]
FIRST_TRAIN_SEASON = 2023  # 2022 is only used as the prior for 2023


def to_arrays(rows: list[FeatureRow], features: list[str] = FEATURES):
    X = np.array([[r.features[f] for f in features] for r in rows], dtype=float)
    y = np.array([r.target for r in rows], dtype=float)
    weeks = np.array([r.season * 100 + r.week for r in rows])
    return X, y, weeks


def choose_alpha(rows: list[FeatureRow], features: list[str] = FEATURES,
                 signs: dict[str, int] = EXPECTED_SIGNS) -> float:
    """Leave-one-season-out cross-validation on the training seasons (by MAE)."""
    seasons = sorted({r.season for r in rows})
    if len(seasons) < 2:
        return 30.0
    best = None
    for alpha in ALPHAS:
        errors = []
        for held in seasons:
            train = [r for r in rows if r.season != held]
            test = [r for r in rows if r.season == held]
            X, y, _ = to_arrays(train, features)
            Xt, yt, _ = to_arrays(test, features)
            model = RidgeModel.fit(X, y, features, alpha, signs=signs)
            errors.append(np.abs(yt - model.predict(Xt)).mean())
        score = float(np.mean(errors))
        if best is None or score < best[1]:
            best = (alpha, score)
    return best[0]


def backtest(rows: list[FeatureRow], test_season: int) -> dict:
    """Fit on seasons before `test_season`, score on it, alongside baselines."""
    train = [r for r in rows if r.season < test_season]
    test = [r for r in rows if r.season == test_season]
    X, y, _ = to_arrays(train)
    Xt, yt, wt = to_arrays(test)
    alpha = choose_alpha(train)
    model = RidgeModel.fit(X, y, FEATURES, alpha, signs=EXPECTED_SIGNS)
    unconstrained = RidgeModel.fit(X, y, FEATURES, alpha)

    vegas_idx = [FEATURES.index("opp_implied_points")]
    vegas = RidgeModel.fit(X[:, vegas_idx], y, ["opp_implied_points"], alpha)
    dst_ppg = Xt[:, FEATURES.index("def_dst_ppg")]
    return {
        "test_season": test_season,
        "train_seasons": sorted({r.season for r in train}),
        "alpha": alpha,
        "model": evaluate(yt, model.predict(Xt), wt),
        "dropped_for_sign": model.metadata.get("dropped_for_sign", []),
        "baselines": {
            "unconstrained_ridge": evaluate(yt, unconstrained.predict(Xt), wt),
            "league_average": evaluate(yt, np.full(len(yt), y.mean()), wt),
            "defense_season_average": evaluate(yt, dst_ppg, wt),
            "vegas_only": evaluate(yt, vegas.predict(Xt[:, vegas_idx]), wt),
        },
    }


def train(conn: sqlite3.Connection, seasons: list[int]) -> tuple[RidgeModel, list[dict]]:
    builder = DefenseFeatureBuilder(conn)
    rows = [r for r in builder.rows(seasons) if r.target is not None]
    if not rows:
        raise RuntimeError("No played games with data. Run `python -m app.data.collect --seasons 2022-2026` first.")
    played_seasons = sorted({r.season for r in rows})
    backtests = [backtest(rows, s) for s in played_seasons[1:]]

    alpha = choose_alpha(rows)
    X, y, _ = to_arrays(rows)
    model = RidgeModel.fit(X, y, FEATURES, alpha, signs=EXPECTED_SIGNS, metadata={
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seasons": played_seasons,
        "games": len(rows),
        "backtests": backtests,
    })
    return model, backtests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first-season", type=int, default=FIRST_TRAIN_SEASON)
    parser.add_argument("--last-season", type=int, default=datetime.now().year)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    conn = sqlite3.connect(settings.database_path)
    model, backtests = train(conn, list(range(args.first_season, args.last_season + 1)))
    model.save(MODEL_PATH)

    for bt in backtests:
        log.info("\nTest season %s (trained on %s, alpha %s)", bt["test_season"], bt["train_seasons"], bt["alpha"])
        log.info("  %-24s %6s %6s %7s %10s", "", "MAE", "RMSE", "R2", "rank corr")
        for name, m in [("model", bt["model"])] + list(bt["baselines"].items()):
            log.info("  %-24s %6.3f %6.3f %7.4f %10s", name, m["mae"], m["rmse"], m["r2"], m["weekly_rank_corr"])
    log.info("\nFinal model: %d games, seasons %s, alpha %s", model.metadata["games"], model.metadata["seasons"], model.alpha)
    log.info("Dropped (wrong direction once correlated features are included): %s",
             ", ".join(model.metadata.get("dropped_for_sign", [])) or "none")
    log.info("Points per 1 SD change (largest first):")
    for name, coef in sorted(zip(model.features, model.coefs), key=lambda t: -abs(t[1])):
        log.info("  %-24s %+.3f", name, coef)
    log.info("\nSaved to %s", MODEL_PATH)


if __name__ == "__main__":
    main()
