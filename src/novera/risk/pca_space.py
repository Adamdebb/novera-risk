"""PCA space of horizon moves and a quadratic model of the book, shared by reverse stress (MR-021)
and the stressed PCA Monte Carlo (MR-022), decision round 50.

**Space.** The overlapping ``horizon``-day moves of every factor (shock units, MR-018) are
standardised by their weighted standard deviation, so rates in absolute terms and prices in
returns weigh the same, and decomposed by principal components of the weighted correlation matrix.
A scenario is a vector of *scores* s, one per kept component, each with unit variance in the
history:

    x_f = σ_f · Σ_c W_fc · s_c,   W = V_k · diag(√λ_k)

The Euclidean norm ‖s‖ is the scenario's plausibility: a norm of 3 is a three-sigma move along the
most dangerous direction (framework part B.6). The weights choose the regime (STRESSED by default).

**How many components.** Enough for ``VARIANCE_TARGET`` of the variance, then more until the
reduced space still reproduces the book's worst 1% of historical moves (``TAIL_TARGET`` of their
summed P&L): a reduction that smooths the tail away is not accepted.

**Book.** The sensitivities give a quadratic P&L in factor moves (the challenger VaR's
delta-gamma-vega, MR-010):

    P&L(x) = Σ_j lin_j · y_j + Σ_j quad_j · y_j²,   y_j = (a_j · x) / bump_j

where j runs over the sensitivity factors (a node, a spot, or a surface or commodity-curve family
whose move is the mean of its members). Scope sums (a desk, the firm) of ``lin`` and ``quad`` make
a scope's P&L a quadratic in the scores, which is cheap to search and to simulate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy import sparse

from novera.market_data.history import MarketHistory
from novera.risk import scenario_covariance as sc_cov
from novera.risk.revaluation import Portfolio
from novera.risk.sensitivities import BUMPS

HORIZON = 10
VARIANCE_TARGET = 0.95
TAIL_TARGET = 0.90
TAIL_SHARE = 0.01
MAX_COMPONENTS = 80
COMPONENT_STEP = 5


# --- quadratic book ------------------------------------------------------------------------------
@dataclass
class QuadBook:
    """Sensitivity factors ``rows`` (factor id, bump), the map ``A`` from factor moves to their
    moves in shock units, and per-trade coefficients."""

    factors: list[str]
    rows: list[tuple[str, float]]
    A: sparse.csr_matrix  # J x F, raw (before dividing by the bump)
    bump: np.ndarray  # J
    lin: sparse.csr_matrix  # trades x J
    quad: sparse.csr_matrix  # trades x J
    trades: list[str]
    trade_pos: dict[str, int]

    def scope(self, trade_ids: list[str] | pd.Index) -> tuple[np.ndarray, np.ndarray]:
        idx = [self.trade_pos[t] for t in trade_ids if t in self.trade_pos]
        if not idx:
            z = np.zeros(len(self.rows))
            return z, z.copy()
        return (
            np.asarray(self.lin[idx].sum(axis=0)).ravel(),
            np.asarray(self.quad[idx].sum(axis=0)).ravel(),
        )

    def moves(self, x: np.ndarray) -> np.ndarray:
        """Sensitivity-factor moves in units of their bump, for factor moves ``x`` (rows of paths)."""
        y = (self.A @ np.atleast_2d(x).T).T
        return y / self.bump

    def pnl(self, x: np.ndarray, lin: np.ndarray, quad: np.ndarray) -> np.ndarray:
        y = self.moves(x)
        return y @ lin + (y * y) @ quad


def quad_book(pf: Portfolio, sens: pd.DataFrame, factors: list[str]) -> QuadBook:
    """The quadratic model of ``pf`` over ``factors`` from its sensitivities (theta excluded)."""
    s = sens[(sens["measure"] != "THETA") & sens["trade_id"].isin(set(pf.priced_ids))].copy()
    bump = np.where(s["measure"] == "VEGA", s["bump"], s["measure"].map(BUMPS).fillna(1.0)).astype(float)
    s["bump_used"] = bump
    keys = s[["factor_id", "bump_used"]].drop_duplicates().sort_values(["factor_id", "bump_used"])
    rows = [(r.factor_id, float(r.bump_used)) for r in keys.itertuples(index=False)]
    row_of = {r: i for i, r in enumerate(rows)}
    fpos = {f: i for i, f in enumerate(factors)}
    a_r, a_c, a_v = [], [], []
    for j, (fid, _) in enumerate(rows):
        if fid.endswith(":"):
            members = [f for f in factors if f.startswith(fid)]
            vol = fid.startswith(("VOL:", "SWVOL:"))
            for m in members:
                a_r.append(j)
                a_c.append(fpos[m])
                a_v.append((pf.base.values[m] if vol else 1.0) / len(members))
        elif fid in fpos:
            a_r.append(j)
            a_c.append(fpos[fid])
            a_v.append(1.0)
    A = sparse.csr_matrix((a_v, (a_r, a_c)), shape=(len(rows), len(factors)))  # noqa: N806
    trades = list(pf.priced_ids)
    tpos = {t: i for i, t in enumerate(trades)}
    ti = s["trade_id"].map(tpos).to_numpy()
    ji = np.array([row_of[(f, b)] for f, b in zip(s["factor_id"], s["bump_used"], strict=True)])
    val = s["value"].to_numpy(dtype=float)
    gamma = (s["measure"] == "GAMMA").to_numpy()
    lin_v = np.where(gamma, 0.0, val)  # the bump scaling is in the moves
    quad_v = np.where(gamma, 0.5 * val, 0.0)
    shape = (len(trades), len(rows))
    return QuadBook(
        factors,
        rows,
        A,
        np.array([b for _, b in rows]),
        sparse.csr_matrix((lin_v, (ti, ji)), shape=shape),
        sparse.csr_matrix((quad_v, (ti, ji)), shape=shape),
        trades,
        tpos,
    )


# --- PCA space -----------------------------------------------------------------------------------
@dataclass
class PCASpace:
    factors: list[str]
    sigma: np.ndarray  # F, weighted standard deviation of the horizon moves (0 for a constant factor)
    components: np.ndarray  # F x K_all, V (unit eigenvectors of the correlation matrix)
    eigenvalues: np.ndarray  # K_all, descending
    k: int
    horizon: int
    regime: str
    effective_days: float
    tail: pd.DataFrame
    """k, explained, tail_capture for each size tried (the firm book)."""
    _moves: np.ndarray | None = None

    @property
    def explained(self) -> float:
        return float(self.eigenvalues[: self.k].sum() / self.eigenvalues.sum())

    @property
    def loadings(self) -> np.ndarray:
        """W = V_k · diag(√λ_k): factor moves in σ units per unit score."""
        return self.components[:, : self.k] * np.sqrt(self.eigenvalues[: self.k])

    def shocks(self, scores: np.ndarray) -> np.ndarray:
        """Factor moves (shock units) of one or many score vectors."""
        s = np.atleast_2d(scores)
        return (s @ self.loadings.T) * self.sigma

    def scores(self, x: np.ndarray) -> np.ndarray:
        """Unit-variance scores of factor moves ``x`` (projection on the kept components)."""
        z = np.divide(np.atleast_2d(x), self.sigma, out=np.zeros_like(np.atleast_2d(x)), where=self.sigma > 0)
        return (z @ self.components[:, : self.k]) / np.sqrt(self.eigenvalues[: self.k])

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "horizon_days": self.horizon,
            "regime": self.regime,
            "effective_days": self.effective_days,
            "factors": int((self.sigma > 0).sum()),
            "components": self.k,
            "explained": self.explained,
            "tail_capture": float(self.tail["tail_capture"].iloc[-1]) if len(self.tail) else float("nan"),
        }


def build_space(
    pf: Portfolio,
    history: MarketHistory,
    book: QuadBook,
    episodes: dict[str, tuple[date, date]] | None,
    firm_trades: list[str] | pd.Index,
    horizon: int = HORIZON,
    method: str = "STRESSED",
) -> PCASpace:
    ms = sc_cov.move_set(history, pf.as_of, horizon, pf.universe)
    factors = book.factors  # the book's order, so scores, shocks and sensitivities line up
    x = np.zeros((len(ms.dates), len(factors)))
    present = [i for i, f in enumerate(factors) if f in ms.index]
    x[:, present] = np.nan_to_num(ms.moves[:, ms.cols([factors[i] for i in present])])
    reg = sc_cov.regime(method, history, ms, episodes)
    w = reg.weights / reg.weights.sum()
    mean = w @ x
    xc = (x - mean) * np.sqrt(w)[:, None]
    sigma = np.sqrt((xc * xc).sum(axis=0))
    live = sigma > 1e-12
    z = np.zeros_like(xc)
    z[:, live] = xc[:, live] / sigma[live]
    _, sv, vt = np.linalg.svd(z, full_matrices=False)
    lam = sv**2
    keep = lam > 1e-12 * lam.max()
    lam, comps = lam[keep], vt[keep].T
    for c in range(comps.shape[1]):  # fixed sign: the largest loading is positive
        if comps[np.argmax(np.abs(comps[:, c])), c] < 0:
            comps[:, c] = -comps[:, c]
    space = PCASpace(factors, sigma, comps, lam, 1, horizon, reg.method, reg.effective_days, pd.DataFrame())
    # Choose k: variance first, then the tail of the firm book's own historical moves.
    lin, quad = book.scope(firm_trades)
    actual = book.pnl(x, lin, quad)
    n_tail = max(int(round(TAIL_SHARE * len(actual))), 1)
    worst = np.argsort(actual)[:n_tail]
    base = float(actual[worst].sum())
    k_var = int(np.searchsorted(np.cumsum(lam) / lam.sum(), VARIANCE_TARGET)) + 1
    rows = []
    k = max(k_var, 1)
    chosen = k
    while True:
        space.k = min(k, len(lam))
        rec = space.shocks(space.scores(x[worst]))
        capture = float(book.pnl(rec, lin, quad).sum() / base) if base < 0 else 1.0
        rows.append({"k": space.k, "explained": space.explained, "tail_capture": capture})
        chosen = space.k
        if capture >= TAIL_TARGET or space.k >= min(MAX_COMPONENTS, len(lam)):
            break
        k += COMPONENT_STEP
    space.k = chosen
    space.tail = pd.DataFrame(rows)
    space._moves = x
    return space
