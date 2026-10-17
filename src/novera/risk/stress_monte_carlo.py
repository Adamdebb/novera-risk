"""Stressed Monte Carlo on fat-tailed PCA scores (MR-022, decision round 50).

Ten-day paths of the whole market drawn in the PCA space of the stressed regime (``pca_space``):

    s = z · √((ν − 2) / χ²_ν),   z ~ N(0, I_k),  χ²_ν drawn once per path

is a multivariate Student-t score vector with unit variance in every component, so the paths have
the covariance of the stressed history but extreme moves arrive together across components (a
common scale per path), which independent fat-tailed scores would not give. The same paths with
the scale fixed at one (Gaussian scores) are run beside them, so the cost of the fat tail is
visible. Each path is pushed through the book's quadratic model (delta, gamma, vega, MR-010) for
the firm and every desk.

Price-like factors cannot fall through ``PRICE_FLOOR``; a path that asks for more is floored
factor by factor before it is valued. For information: no limit reads it. The share of paths
losing at least each stress-loss limit ties it to the reverse stress (MR-021).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from novera.risk.pca_space import PCASpace, QuadBook
from novera.risk.revaluation import Portfolio
from novera.risk.reverse_stress import PRICE_FLOOR, Target

RECORD = "MR-022"
MODEL_VERSION = "1.0.0"

PATHS = 10_000
DEGREES_OF_FREEDOM = 4.0
SEED = 50
CHUNK = 2_500
VAR_CONFIDENCE = 0.99
ES_CONFIDENCE = 0.975
EXTREME_CONFIDENCE = 0.999
QUANTILES = (0.5, 0.9, 0.95, 0.975, 0.99, 0.995, 0.999, 0.9995, 0.9999)
FIRM = "(firm)"

COLUMNS = [
    "scope",
    "distribution",
    "paths",
    "mean",
    "worst",
    "var_99",
    "es_975",
    "var_999",
    "tail_ratio",
]


def draw_scores(k: int, paths: int, df: float, rng: np.random.Generator, fat: bool) -> np.ndarray:
    z = rng.standard_normal((paths, k))
    if not fat:
        return z
    chi = rng.chisquare(df, size=paths)
    return z * np.sqrt((df - 2.0) / chi)[:, None]


def _measures(loss: np.ndarray) -> dict[str, float]:
    """Loss (positive) statistics of one scope's paths."""
    srt = np.sort(loss)
    n = len(srt)

    def q(p: float) -> float:
        return float(srt[min(int(np.ceil(p * n)) - 1, n - 1)])

    var99 = q(VAR_CONFIDENCE)
    tail = srt[srt >= q(ES_CONFIDENCE)]
    return {
        "mean": float(-loss.mean()),
        "worst": float(-srt[-1]),
        "var_99": var99,
        "es_975": float(tail.mean()),
        "var_999": q(EXTREME_CONFIDENCE),
    }


@dataclass
class StressMonteCarloResult:
    summary_table: pd.DataFrame
    """scope, distribution, paths, mean, worst (P&L), var_99, es_975, var_999 (losses), tail_ratio."""
    quantiles: pd.DataFrame
    """The firm's loss quantiles, Gaussian and fat-tailed."""
    targets: pd.DataFrame
    """target_id, loss_target, prob_gaussian, prob_fat_tail, return_period_days_fat_tail."""
    parameters: dict[str, object] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {}
        firm = self.summary_table[
            (self.summary_table["scope"] == FIRM) & (self.summary_table["distribution"] == "FAT_TAIL")
        ]
        if len(firm):
            r = firm.iloc[0]
            out.update(
                stress_mc_var_99=float(r["var_99"]),
                stress_mc_es_975=float(r["es_975"]),
                stress_mc_var_999=float(r["var_999"]),
                stress_mc_tail_ratio=float(r["tail_ratio"]),
            )
        return out


def run_stress_monte_carlo(
    pf: Portfolio,
    book: QuadBook,
    space: PCASpace,
    scopes: dict[str, list[str]],
    targets: list[Target],
    paths: int = PATHS,
    df: float = DEGREES_OF_FREEDOM,
    seed: int = SEED,
) -> StressMonteCarloResult:
    """Gaussian and fat-tailed stressed paths for the firm (``FIRM``) and each named scope."""
    names = [FIRM, *scopes]
    sets = [list(book.trades), *(scopes[n] for n in scopes)]
    lin = np.column_stack([book.scope(t)[0] for t in sets])
    quad = np.column_stack([book.scope(t)[1] for t in sets])
    rel_ids = {f for f, rf in pf.universe.items() if rf.shock_type != "ABSOLUTE"}
    relative = np.array([f in rel_ids for f in space.factors])
    loadings, sigma = space.loadings, space.sigma
    pnl: dict[str, np.ndarray] = {}
    for fat in (False, True):
        rng = np.random.default_rng(seed)  # the same normals for both: only the scale differs
        out = np.empty((paths, len(names)))
        for lo in range(0, paths, CHUNK):
            n = min(CHUNK, paths - lo)
            s = draw_scores(space.k, n, df, rng, fat)
            x = (s @ loadings.T) * sigma
            x[:, relative] = np.maximum(x[:, relative], PRICE_FLOOR)
            y = book.moves(x)
            out[lo : lo + n] = y @ lin + (y * y) @ quad
        pnl["FAT_TAIL" if fat else "GAUSSIAN"] = out
    rows = []
    for dist, mat in pnl.items():
        for j, name in enumerate(names):
            m = _measures(-mat[:, j])
            ref = _measures(-pnl["GAUSSIAN"][:, j])["var_999"]
            rows.append(
                {
                    "scope": name,
                    "distribution": dist,
                    "paths": paths,
                    **m,
                    "tail_ratio": m["var_999"] / ref if ref > 0 else float("nan"),
                }
            )
    firm_g, firm_f = -pnl["GAUSSIAN"][:, 0], -pnl["FAT_TAIL"][:, 0]
    quantiles = pd.DataFrame(
        {
            "quantile": QUANTILES,
            "loss_gaussian": [float(np.quantile(firm_g, q)) for q in QUANTILES],
            "loss_fat_tail": [float(np.quantile(firm_f, q)) for q in QUANTILES],
        }
    )
    trows = []
    for t in targets:
        j = (
            names.index(FIRM if t.level == "FIRM" else t.entity_id)
            if (t.level == "FIRM" or t.entity_id in scopes)
            else None
        )
        if j is None:
            continue
        pg = float((-pnl["GAUSSIAN"][:, j] >= t.loss).mean())
        pf_ = float((-pnl["FAT_TAIL"][:, j] >= t.loss).mean())
        trows.append(
            {
                "target_id": t.target_id,
                "loss_target": t.loss,
                "prob_gaussian": pg,
                "prob_fat_tail": pf_,
                "return_period_years": (1.0 / pf_) * 10.0 / 252.0 if pf_ > 0 else float("nan"),
            }
        )
    params = {
        "paths": paths,
        "degrees_of_freedom": df,
        "seed": seed,
        "horizon_days": space.horizon,
        "components": space.k,
        "regime": space.regime,
    }
    return StressMonteCarloResult(
        pd.DataFrame(rows, columns=COLUMNS),
        quantiles,
        pd.DataFrame(
            trows,
            columns=["target_id", "loss_target", "prob_gaussian", "prob_fat_tail", "return_period_years"],
        ),
        params,
    )
