"""Ridge regression for DST points, with per-feature explanations.

Features are standardized (z-scores using the training data), so each coefficient is the
change in projected points for a one-standard-deviation change in that feature, and a
prediction breaks down exactly into intercept + sum of per-feature contributions.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class RidgeModel:
    features: list[str]
    means: np.ndarray
    scales: np.ndarray
    coefs: np.ndarray
    intercept: float
    alpha: float
    metadata: dict

    @classmethod
    def fit(
        cls,
        X: np.ndarray,
        y: np.ndarray,
        features: list[str],
        alpha: float,
        metadata: dict | None = None,
        signs: dict[str, int] | None = None,
        weights: np.ndarray | None = None,
    ):
        """Ridge fit. With `signs` ({feature: +1 or -1}), any feature whose coefficient comes
        out in the wrong direction is dropped (coefficient 0) and the rest refit, one at a
        time, until every coefficient points the expected way. Correlated features (like
        Vegas lines and points per game) otherwise produce flipped signs that make the
        per-feature explanations misleading.

        `weights` (one per row) weights the fit, the standardization, and the intercept,
        e.g. to count recent seasons more."""
        w = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float) / np.mean(weights)
        means = np.average(X, axis=0, weights=w)
        scales = np.sqrt(np.average((X - means) ** 2, axis=0, weights=w))
        scales[scales == 0] = 1.0
        Z = (X - means) / scales
        intercept = float(np.average(y, weights=w))
        active = list(range(len(features)))
        while True:
            coefs = np.zeros(len(features))
            if active:
                Za = Z[:, active]
                # Closed-form weighted ridge on centered data: (Z'WZ + alpha I)^-1 Z'W(y - mean)
                A = Za.T @ (w[:, None] * Za) + alpha * np.eye(len(active))
                coefs[active] = np.linalg.solve(A, Za.T @ (w * (y - intercept)))
            if not signs:
                break
            wrong = [i for i in active if signs.get(features[i], 0) * coefs[i] < 0]
            if not wrong:
                break
            active.remove(max(wrong, key=lambda i: abs(coefs[i])))
        dropped = [features[i] for i in range(len(features)) if i not in active]
        meta = {**(metadata or {}), "dropped_for_sign": dropped} if signs else (metadata or {})
        return cls(features, means, scales, coefs, intercept, alpha, meta)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.intercept + ((X - self.means) / self.scales) @ self.coefs

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        """Points each feature adds or removes versus an average matchup."""
        z = (x - self.means) / self.scales
        return {name: float(value) for name, value in zip(self.features, z * self.coefs)}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "features": self.features,
            "means": self.means.tolist(),
            "scales": self.scales.tolist(),
            "coefs": self.coefs.tolist(),
            "intercept": self.intercept,
            "alpha": self.alpha,
            "metadata": self.metadata,
        }, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RidgeModel":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d["features"], np.array(d["means"]), np.array(d["scales"]), np.array(d["coefs"]),
                   d["intercept"], d["alpha"], d.get("metadata", {}))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Rank correlation (ties broken by order, fine for continuous predictions)."""
    if len(a) < 3:
        return float("nan")
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(y: np.ndarray, pred: np.ndarray, weeks: np.ndarray) -> dict:
    """MAE, RMSE, R^2, and average weekly rank correlation (how well it orders defenses)."""
    err = y - pred
    ss_res, ss_tot = float((err ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    weekly = [spearman(y[weeks == w], pred[weeks == w]) for w in np.unique(weeks)]
    weekly = [r for r in weekly if not np.isnan(r)]
    return {
        "n": int(len(y)),
        "mae": round(float(np.abs(err).mean()), 3),
        "rmse": round(float(np.sqrt((err ** 2).mean())), 3),
        "r2": round(1 - ss_res / ss_tot, 4) if ss_tot else 0.0,
        "weekly_rank_corr": round(float(np.mean(weekly)), 4) if weekly else None,
    }
