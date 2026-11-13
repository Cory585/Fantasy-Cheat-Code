"""Train and evaluate the kicker model.

Usage (from backend/, after `python -m app.data.collect --seasons 2022-2026`):
    python -m app.kicker.train

Same method as the DST model (`app.defense.train`): fit on earlier seasons, score on a later
one against simple baselines, then fit on every played game and save to
`backend/data/kicker_model.json`.
"""

import argparse
import logging
import sqlite3
from datetime import datetime, timezone

import numpy as np

from app.config import DATA_DIR, settings
from app.defense.model import RidgeModel, evaluate
from app.defense.train import FIRST_TRAIN_SEASON, choose_alpha, to_arrays
from app.kicker.features import EXPECTED_SIGNS, FEATURES, KickerFeatureBuilder

log = logging.getLogger(__name__)

MODEL_PATH = DATA_DIR / "kicker_model.json"


def backtest(rows, test_season: int) -> dict:
    train = [r for r in rows if r.season < test_season]
    test = [r for r in rows if r.season == test_season]
    X, y, _ = to_arrays(train, FEATURES)
    Xt, yt, wt = to_arrays(test, FEATURES)
    alpha = choose_alpha(train, FEATURES, EXPECTED_SIGNS)
    model = RidgeModel.fit(X, y, FEATURES, alpha, signs=EXPECTED_SIGNS)

    def col(name, M=Xt):
        return M[:, FEATURES.index(name)]

    vegas_idx = [FEATURES.index("team_implied_points")]
    vegas = RidgeModel.fit(X[:, vegas_idx], y, ["team_implied_points"], alpha)
    league_allowed = col("opp_k_allowed_pg", X).mean()
    return {
        "test_season": test_season,
        "train_seasons": sorted({r.season for r in train}),
        "alpha": alpha,
        "model": evaluate(yt, model.predict(Xt), wt),
        "dropped_for_sign": model.metadata.get("dropped_for_sign", []),
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
    rows = [r for r in KickerFeatureBuilder(conn).rows(seasons) if r.target is not None]
    if not rows:
        raise RuntimeError("No played games with data. Run `python -m app.data.collect --seasons 2022-2026` first.")
    played_seasons = sorted({r.season for r in rows})
    backtests = [backtest(rows, s) for s in played_seasons[1:]]
    alpha = choose_alpha(rows, FEATURES, EXPECTED_SIGNS)
    X, y, _ = to_arrays(rows, FEATURES)
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
        if coef:
            log.info("  %-24s %+.3f", name, coef)
    log.info("\nSaved to %s", MODEL_PATH)


if __name__ == "__main__":
    main()
