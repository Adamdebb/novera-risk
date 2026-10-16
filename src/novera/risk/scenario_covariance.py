"""Covariance of horizon moves, conditional scenario expansion, PCA shapes and plausibility
(MR-018, decision round 47).

A consistent scenario moves every factor, not only the ones its rules name. The rules keep their
exact shocks; each rule nominates one representative factor (an anchor: the S&P 500 for an
equity rule, the USD 10Y node for a rates rule), and every factor no rule names takes its
conditional expectation given the anchors' shocks:

    E[x_o | x_a = r] = Σ_oa Σ_aa⁻¹ r

where Σ is the weighted covariance of overlapping ``horizon``-day moves in shock units (absolute
changes for rates, spreads and SABR rho; simple returns for prices and vols) over the stored
history up to the valuation date. Only the anchors' block is inverted (a few dozen variables),
so the 1,584-factor covariance never needs to be. This is the market-driven scenario method of
Golub, Greenberg and Ratcliffe (BlackRock's Guide, ch. 5), implemented from the description.

Weights choose the regime the correlations come from:
- STRESSED: the stored stress episodes and the fifth of days with the highest 20-day realised
  volatility of the S&P 500 (crisis correlations).
- SIMILAR: each day weighted by how closely its anchor moves point the scenario's way (cosine
  similarity of standardised moves, to the fourth power); falls back to STRESSED when fewer than
  30 effective days resemble the scenario.
- RECENT: exponential decay with a 250-day half-life.

Plausibility is measured on the anchors with the same covariance: the scenario z-score is the
Mahalanobis distance divided by √n (n anchors), its exceedance probability the share of
historical horizon moves with a larger z-score, the correlation z-score the ratio of the
z-score to the one with correlations set to zero (above 1: the signs and relative sizes fight
the covariance), and each anchor's z-score delta how much the score falls when that anchor
takes its conditional expectation given the others.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from novera.market_data.history import MarketHistory
from novera.market_data.risk_factors import RiskFactor
from novera.risk.scenarios import historical_shocks

METHODS = ("STRESSED", "SIMILAR", "RECENT")
DEFAULT_METHOD = "STRESSED"
DEFAULT_HORIZON = 20
RECENT_HALF_LIFE = 250
STRESS_QUANTILE = 0.8
SIMILAR_MIN_DAYS = 30.0
FILL_CAP = 1.5  # a filled shock never exceeds 1.5x the factor's largest historical horizon move
RIDGE = 1e-6

HEADLINE_FACTORS: tuple[tuple[str, str], ...] = (
    ("EQIDX:SPX", "S&P 500"),
    ("EQIDX:SX5E", "Euro Stoxx 50"),
    ("IR:USD:2Y", "USD 2Y zero"),
    ("IR:USD:10Y", "USD 10Y zero"),
    ("IR:EUR:10Y", "EUR 10Y zero"),
    ("FX:EURUSD", "EUR/USD"),
    ("FX:USDJPY", "USD/JPY"),
    ("CDS:CDX.NA.IG", "CDX IG"),
    ("CDS:CDX.NA.HY", "CDX HY"),
    ("VOL:SPX:3M:1.00", "SPX 3M ATM vol"),
    ("SWVOL:USD:1Y:10Y", "USD 1Y10Y swaption vol"),
    ("CMD:BRENT:1M", "Brent front"),
    ("CMD:GOLD:1M", "Gold front"),
    ("CRYPTO:BTC", "Bitcoin"),
)
"""The headline basket: the stress library's summary factors and the risk ruler's coordinates."""

PREFERRED_ANCHORS = (
    "EQIDX:SPX",
    "EQIDX:SX5E",
    "IR:USD:10Y",
    "IR:USD:2Y",
    "IR:EUR:10Y",
    "FX:EURUSD",
    "FX:USDJPY",
    "CDS:CDX.NA.IG",
    "CDS:CDX.NA.HY",
    "VOL:SPX:3M:1.00",
    "CMD:BRENT:1M",
    "CMD:WTI:1M",
    "CMD:GOLD:1M",
    "CRYPTO:BTC",
    "CRYPTO:ETH",
)
_CCY_RANK = {c: i for i, c in enumerate(("USD", "EUR", "GBP", "JPY", "CHF"))}
_TENOR = re.compile(r"^(\d+)([DWMY])$")


def tenor_months(token: str) -> float | None:
    m = _TENOR.match(token)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return {"D": n / 30.0, "W": n / 4.3, "M": float(n), "Y": 12.0 * n}[u]


def node_key(factor_id: str, prefix: str) -> tuple:
    """Order of a curve or surface node: tenors in months, then numbers, then text."""
    out: list[tuple[int, float | str]] = []
    for tok in factor_id[len(prefix) :].split(":"):
        t = tenor_months(tok)
        if t is not None:
            out.append((0, t))
            continue
        try:
            out.append((1, float(tok)))
        except ValueError:
            out.append((2, tok))
    return tuple(out)


# --- horizon moves -----------------------------------------------------------------------------
@dataclass
class MoveSet:
    """Overlapping ``horizon``-day moves of every factor, in shock units, ending at ``as_of``."""

    moves: np.ndarray  # rows: move end dates, columns: factors
    dates: list[date]
    factors: list[str]
    horizon: int
    index: dict[str, int] = field(default_factory=dict)
    _scale: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.index = {f: i for i, f in enumerate(self.factors)}

    def cols(self, ids: list[str]) -> np.ndarray:
        return np.array([self.index[f] for f in ids], dtype=int)

    @property
    def max_abs(self) -> np.ndarray:
        if self._scale is None:
            m = self.moves
            self._scale = np.nanmax(np.abs(m), axis=0) if len(m) else np.zeros(len(self.factors))
        return self._scale


_CACHE: dict[tuple, MoveSet] = {}


def move_set(
    history: MarketHistory, as_of: date, horizon: int, universe: dict[str, RiskFactor] | None = None
) -> MoveSet:
    """Every overlapping horizon move in the history up to ``as_of``; cached per history object."""
    key = (id(history), as_of, horizon, len(history.wide.columns))
    if key in _CACHE:
        return _CACHE[key]
    n = sum(1 for d in history.dates if d <= as_of)
    if n <= horizon + 1:
        raise ValueError(f"the history up to {as_of} is too short for {horizon}-day moves")
    m = historical_shocks(history, as_of, n - horizon, horizon, universe)
    ms = MoveSet(m.to_numpy(dtype=float), list(m.index), list(m.columns), horizon)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = ms
    return ms


# --- regime weights ----------------------------------------------------------------------------
def stressed_days(
    history: MarketHistory, ms: MoveSet, episodes: dict[str, tuple[date, date]] | None
) -> np.ndarray:
    """1 for a move ending inside a stored stress episode or on a day of top-quintile 20-day
    realised S&P 500 volatility, else 0."""
    flag = np.zeros(len(ms.dates))
    for start, end in (episodes or {}).values():
        flag[[start <= d <= end for d in ms.dates]] = 1.0
    if "EQIDX:SPX" in history.wide.columns:
        px = history.wide["EQIDX:SPX"].astype(float)
        vol = np.log(px).diff().rolling(20).std()
        cut = float(np.nanquantile(vol.to_numpy(), STRESS_QUANTILE))
        hi = {d for d, v in vol.items() if v >= cut}
        flag[[d in hi for d in ms.dates]] = 1.0
    if flag.sum() < 30:  # too few stressed days to estimate anything: every day counts
        flag[:] = 1.0
    return flag


def recent_weights(ms: MoveSet) -> np.ndarray:
    t = np.arange(len(ms.dates))
    return 0.5 ** ((len(t) - 1 - t) / RECENT_HALF_LIFE)


def similar_weights(ms: MoveSet, anchor_cols: np.ndarray, shock: np.ndarray) -> tuple[np.ndarray, float]:
    x = ms.moves[:, anchor_cols]
    sd = np.nanstd(x, axis=0)
    sd[sd == 0] = 1.0
    z, s = np.nan_to_num(x / sd), shock / sd
    norms = np.linalg.norm(z, axis=1) * (np.linalg.norm(s) or 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.where(norms > 0, z @ s / norms, 0.0)
    w = np.clip(cos, 0.0, None) ** 4
    eff = float(w.sum() ** 2 / (w**2).sum()) if (w**2).sum() > 0 else 0.0
    return w, eff


def weighted_moments(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted mean and demeaned matrix scaled by √w, so ``a.T @ b`` is a covariance block."""
    w = w / w.sum()
    x = np.nan_to_num(x)
    mean = w @ x
    return mean, (x - mean) * np.sqrt(w)[:, None]


@dataclass
class Regime:
    method: str  # the method used (SIMILAR may fall back to STRESSED)
    weights: np.ndarray
    effective_days: float


def regime(
    method: str,
    history: MarketHistory,
    ms: MoveSet,
    episodes: dict[str, tuple[date, date]] | None,
    anchor_cols: np.ndarray | None = None,
    shock: np.ndarray | None = None,
) -> Regime:
    if method == "RECENT":
        w = recent_weights(ms)
    elif method == "SIMILAR" and anchor_cols is not None and shock is not None and len(anchor_cols):
        w, eff = similar_weights(ms, anchor_cols, shock)
        if eff >= SIMILAR_MIN_DAYS:
            return Regime("SIMILAR", w, eff)
        return regime("STRESSED", history, ms, episodes)
    else:
        w = stressed_days(history, ms, episodes)
        method = "STRESSED"
    return Regime(method, w, float(w.sum() ** 2 / (w**2).sum()))


# --- conditional expansion ---------------------------------------------------------------------
def _inv(s: np.ndarray) -> np.ndarray:
    k = len(s)
    ridge = RIDGE * (float(np.trace(s)) / k if k else 1.0)
    return np.linalg.inv(s + ridge * np.eye(k))


def representative(matched: list[str], ms: MoveSet) -> str | None:
    """The anchor that stands for a rule: a headline factor when the rule shocks one, else the
    10Y, 2Y or 5Y node of the leading currency, a 3M at-the-money vol, else the most volatile."""
    inside = [f for f in matched if f in ms.index]
    if not inside:
        return None
    s = set(inside)
    for p in PREFERRED_ANCHORS:
        if p in s:
            return p
    for tenor in ("10Y", "2Y", "5Y", "1Y", "1M"):
        c = [f for f in inside if f.endswith(":" + tenor)]
        if c:
            return sorted(c, key=lambda f: (_CCY_RANK.get(f.split(":")[1], 99), f))[0]
    c = [f for f in inside if ":3M:1.00" in f]
    if c:
        return sorted(c)[0]
    var = np.nanvar(ms.moves[:, ms.cols(inside)], axis=0)
    return inside[int(np.argmax(var))]


@dataclass
class Expansion:
    shocks: dict[str, float]
    anchors: dict[str, float]
    method: str
    effective_days: float
    filled: int


def expand(
    named: dict[str, float],
    anchors: dict[str, float],
    ms: MoveSet,
    reg: Regime,
    relative: set[str],
) -> Expansion:
    """Fill every factor ``named`` leaves out with its conditional expectation given ``anchors``."""
    ids = [a for a in anchors if a in ms.index]
    if not ids:
        return Expansion(dict(named), {}, reg.method, reg.effective_days, 0)
    a_cols = ms.cols(ids)
    r = np.array([anchors[a] for a in ids])
    _, xc = weighted_moments(ms.moves, reg.weights)
    s_aa = xc[:, a_cols].T @ xc[:, a_cols]
    s_oa = xc.T @ xc[:, a_cols]
    fill = s_oa @ (_inv(s_aa) @ r)
    cap = FILL_CAP * ms.max_abs
    fill = np.clip(fill, -cap, cap)
    out = dict(named)
    n = 0
    for f, v in zip(ms.factors, fill, strict=True):
        if f in out or not math.isfinite(v) or v == 0.0:
            continue
        out[f] = max(float(v), -0.95) if f in relative else float(v)
        n += 1
    return Expansion(out, {a: anchors[a] for a in ids}, reg.method, reg.effective_days, n)


# --- PCA shapes --------------------------------------------------------------------------------
@dataclass
class PCAShape:
    shocks: dict[str, float]
    explained: float  # share of the block's variance the component carries
    nodes: int


def pca_shape(ms: MoveSet, reg: Regime, prefix: str, component: int, sigmas: float) -> PCAShape:
    """``sigmas`` standard deviations of principal component ``component`` (1 level, 2 slope,
    3 curvature) of the nodes under ``prefix``. Signs are fixed so a positive size raises the
    curve (1), raises the long end against the short end (2), raises the belly (3)."""
    nodes = sorted((f for f in ms.factors if f.startswith(prefix)), key=lambda f: node_key(f, prefix))
    if len(nodes) < 3:
        raise ValueError(f"{prefix} has {len(nodes)} nodes in the history; PCA needs at least 3")
    _, xc = weighted_moments(ms.moves[:, ms.cols(nodes)], reg.weights)
    cov = xc.T @ xc
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    k = component - 1
    v = vecs[:, k].copy()
    pos = np.linspace(-1.0, 1.0, len(v))
    if k == 0:
        sign = np.sign(v.sum())
    elif k == 1:
        sign = np.sign(v @ pos)
    else:
        sign = np.sign(v @ (1.0 - 2.0 * np.abs(pos)))
    v *= sign or 1.0
    size = sigmas * math.sqrt(max(float(vals[k]), 0.0))
    total = float(vals.clip(min=0).sum()) or 1.0
    shocks = {f: float(size * x) for f, x in zip(nodes, v, strict=True)}
    return PCAShape(shocks, float(max(vals[k], 0)) / total, len(nodes))


# --- plausibility ------------------------------------------------------------------------------
def _z(r: np.ndarray, inv: np.ndarray) -> float:
    return math.sqrt(max(float(r @ inv @ r), 0.0) / len(r)) if len(r) else 0.0


@dataclass
class Plausibility:
    z_score: float
    mahalanobis: float
    volatility_z: float
    correlation_z: float
    exceedance: float
    anchors: list[dict[str, Any]]
    method: str
    horizon: int


def plausibility(anchors: dict[str, float], ms: MoveSet, reg: Regime) -> Plausibility | None:
    ids = [a for a in anchors if a in ms.index]
    if not ids:
        return None
    cols = ms.cols(ids)
    r = np.array([anchors[a] for a in ids])
    _, xc = weighted_moments(ms.moves[:, cols], reg.weights)
    s = xc.T @ xc
    inv = _inv(s)
    z = _z(r, inv)
    sd = np.sqrt(np.clip(np.diag(s), 1e-300, None))
    fz = r / sd
    vz = math.sqrt(float(fz @ fz) / len(fz))
    hist = np.nan_to_num(ms.moves[:, cols])
    zt = np.sqrt(np.clip(np.einsum("ij,jk,ik->i", hist, inv, hist), 0, None) / len(ids))
    exceed = float((zt >= z).mean()) if len(zt) else 0.0
    rows = []
    for i, a in enumerate(ids):
        others = [j for j in range(len(ids)) if j != i]
        if others:
            cond = float(s[i, others] @ _inv(s[np.ix_(others, others)]) @ r[others])
            r2 = r.copy()
            r2[i] = cond
            delta = z - _z(r2, inv)
        else:
            cond, delta = 0.0, z
        rows.append(
            {
                "factor_id": a,
                "shock": float(r[i]),
                "factor_z": float(fz[i]),
                "expected_given_others": cond,
                "z_delta": float(delta),
            }
        )
    return Plausibility(
        z, z * math.sqrt(len(ids)), vz, (z / vz) if vz else 0.0, exceed, rows, reg.method, ms.horizon
    )


def basket_z(shocks: dict[str, float], ms: MoveSet, reg: Regime) -> float | None:
    """The z-score of a scenario on the headline basket, the risk ruler's common coordinates.
    Factors the scenario leaves at zero count as zero moves."""
    ids = [f for f, _ in HEADLINE_FACTORS if f in ms.index]
    if not ids:
        return None
    _, xc = weighted_moments(ms.moves[:, ms.cols(ids)], reg.weights)
    return _z(np.array([shocks.get(f, 0.0) for f in ids]), _inv(xc.T @ xc))
