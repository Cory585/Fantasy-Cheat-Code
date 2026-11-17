"""Kicker and DST matchup models, applied to this season's games for start/sit.

The models are trained offline (`python -m app.defense.train`, `python -m app.kicker.train`)
and saved as JSON next to the database. Without the saved files, nothing here is used and
start/sit keeps its plain matchup factors.

For every game this season (played or not), each team gets a predicted kicker score and a
predicted DST score, with the features that moved each prediction most. Played games also
record the defense's actual fantasy points, which the player stats don't include.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.defense.model import RidgeModel
from app.kicker.features import KickerFeatureBuilder

log = logging.getLogger(__name__)

MAX_FACTORS = 3
MIN_FACTOR_POINTS = 0.15  # smaller effects aren't worth a line in the reasons

# Plain-language descriptions, phrased so "helps" / "hurts" reads naturally after them.
DST_LABELS = {
    "opp_implied_points": "opponent's Vegas total",
    "opp_sack_rate": "opponent's sack rate allowed",
    "opp_proe": "opponent's pass-heavy play-calling",
    "opp_plays_pg": "opponent pace",
    "opp_qb_sack_rate": "opposing QB's sack rate",
    "opp_qb_int_rate": "opposing QB's interception rate",
    "def_takeaways_pg": "takeaways per game",
    "def_pressure_pg": "QB hits per game",
    "def_pass_defended_pg": "passes defended per game",
    "def_players_out": "defensive health",
    "def_favored_by": "point spread",
    "wind_mph": "wind",
    "exp_pa_score": "expected points-allowed score",
    "exp_takeaways": "expected takeaways",
}
# Games without a Vegas line yet use an estimate of it (see DefenseFeatureBuilder.estimated_points).
DST_LABELS_WITHOUT_LINE = {
    **DST_LABELS,
    "opp_implied_points": "opponent's projected points",
    "def_favored_by": "projected point spread",
}
KICKER_LABELS = {
    "team_implied_points": "team's Vegas total",
    "team_favored_by": "point spread",
    "fg_pct": "field goal accuracy",
    "long_fg_share": "50+ yard attempts",
    "pat_pct": "extra point accuracy",
    "opp_k_allowed_pg": "kicker points the opponent allows",
    "indoor": "dome",
    "wind_mph": "wind",
    "cold": "cold",
    "altitude": "altitude",
    "home": "home field",
}
KICKER_LABELS_WITHOUT_LINE = {
    **KICKER_LABELS,
    "team_implied_points": "team's projected points",
    "team_favored_by": "projected point spread",
}


@dataclass(frozen=True)
class ModelEstimate:
    points: float
    # (description, points versus an average matchup), largest effects first.
    factors: tuple[tuple[str, float], ...] = ()


@dataclass
class TeamModels:
    # team -> week -> estimate
    dst: dict[str, dict[int, ModelEstimate]] = field(default_factory=dict)
    kicker: dict[str, dict[int, ModelEstimate]] = field(default_factory=dict)
    # team -> week -> the defense's actual fantasy points (played games)
    dst_actual: dict[str, dict[int, float]] = field(default_factory=dict)


def load_team_models(conn: sqlite3.Connection, season: int, dst_path: Path | None,
                     kicker_path: Path | None) -> TeamModels:
    dst_model = _load_model(dst_path)
    kicker_model = _load_model(kicker_path)
    builder = KickerFeatureBuilder(conn, first_season=season - 1)
    models = TeamModels()

    # Games without a Vegas line use the line estimate each model was trained with. Models saved
    # before it was recorded fit it on the loaded games instead.
    builder.use_line_coefs(_line_coefs(dst_model))
    for row in builder.dst_rows([season], include_unplayed=True):
        if row.target is not None:
            models.dst_actual.setdefault(row.defense, {})[row.week] = round(row.target, 2)
        if dst_model is not None:
            labels = DST_LABELS_WITHOUT_LINE if row.context.get("line_estimated") else DST_LABELS
            models.dst.setdefault(row.defense, {})[row.week] = _estimate(dst_model, row, labels)
    if kicker_model is not None:
        builder.use_line_coefs(_line_coefs(kicker_model))
        for row in builder.rows([season], include_unplayed=True):
            labels = KICKER_LABELS_WITHOUT_LINE if row.context.get("line_estimated") else KICKER_LABELS
            models.kicker.setdefault(row.defense, {})[row.week] = _estimate(kicker_model, row, labels)
    return models


def _load_model(path: Path | None) -> RidgeModel | None:
    if path is None or not path.exists():
        return None
    try:
        return RidgeModel.load(path)
    except (OSError, ValueError, KeyError) as error:
        log.error("Couldn't load model %s: %s", path, error)
        return None


def _line_coefs(model: RidgeModel | None) -> list[float] | None:
    """The line-estimate coefficients saved with the model, if it has usable ones."""
    coefs = model.metadata.get("line_coefs") if model is not None else None
    if isinstance(coefs, list) and len(coefs) == 4 and all(isinstance(v, (int, float)) for v in coefs):
        return coefs
    return None


def _estimate(model: RidgeModel, row, labels: dict[str, str]) -> ModelEstimate:
    # Features the saved model doesn't know (trained before they were added) are ignored, and
    # features it knows but this build lacks count as average.
    x = np.array([row.features.get(name, mean) for name, mean in zip(model.features, model.means)], dtype=float)
    contributions = model.contributions(x)
    ranked = sorted(((labels.get(name, name), value) for name, value in contributions.items()
                     if abs(value) >= MIN_FACTOR_POINTS), key=lambda t: -abs(t[1]))
    return ModelEstimate(points=round(float(model.predict(x[None, :])[0]), 2),
                         factors=tuple((label, round(value, 2)) for label, value in ranked[:MAX_FACTORS]))
