"""Reverse stress test in PCA space (MR-021, decision round 50).

Forward stress asks what a scenario costs; reverse stress asks what scenario costs a given amount
and how plausible it is. The targets are the stress-loss limits (the firm's and each desk's): the
engine finds the market moves that lose exactly the limit.

**Search.** In the space of principal-component scores (``pca_space``), a scenario is a score
vector s, its plausibility is the Euclidean norm ‖s‖, and the book's loss is a quadratic

    loss(s) = −gᵀs − sᵀQs

from the sensitivities. The most plausible scenario that reaches the target L solves

    minimise ‖s‖²   subject to   loss(s) ≥ L,  ‖s‖ ≤ CAP

(sequential quadratic programming, started on the linear solution). Further solutions solve the
same problem with an angle constraint: each must point at least ``MIN_ANGLE_DEG`` away from every
solution found so far, so they are different stories, not the same story at a slightly different
size.

**Neighbours.** For each solution the engine rotates it by ``NEIGHBOUR_ANGLE_DEG`` towards the
directions along which the loss grows fastest, rescales each to reach the target again, and reports
how much larger its norm is. Neighbours of almost the same norm mean the plausibility surface is
flat there: the story is one of a family.

**Confirmation.** The sensitivity approximation searches; full revaluation of the trades in scope
confirms. The solution's factor moves are revalued, then scaled along the same ray by a secant
step until the full-revaluation loss matches the target, so the reported norm is the norm of a
scenario that really loses the target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm as normal

from novera.domain.limits import Limit, LimitType
from novera.limits.monitoring import trades_in_scope
from novera.risk.pca_space import PCASpace, QuadBook
from novera.risk.revaluation import Portfolio
from novera.risk.scenario_covariance import HEADLINE_FACTORS
from novera.risk.scenarios import apply_shocks

RECORD = "MR-021"
MODEL_VERSION = "1.0.0"

SOLUTIONS = 3
MIN_ANGLE_DEG = 45.0
NEIGHBOURS = 4
NEIGHBOUR_ANGLE_DEG = 15.0
CAP = 15.0
"""Largest score norm searched; a target that needs more is reported unreachable."""
DRIVERS = 8
CONFIRM_TOLERANCE = 0.02
SCALE_BOUNDS = (0.05, 6.0)
MAX_REVALUATIONS = 6
PRICE_FLOOR = -0.95
BOUND_ROUNDS = 8
BOUND_BATCH = 25

COLUMNS = [
    "target_id",
    "level",
    "entity_id",
    "loss_target",
    "solution",
    "norm",
    "exceedance",
    "probability",
    "angle_to_first",
    "taylor_loss",
    "full_loss_unscaled",
    "scale",
    "full_loss",
    "refined_norm",
    "status",
    "neighbour_norm_ratio",
]
NEIGHBOUR_COLUMNS = ["target_id", "solution", "neighbour", "angle", "norm", "norm_ratio"]
MOVE_COLUMNS = ["target_id", "solution", "kind", "factor_id", "move", "pnl"]


@dataclass
class Target:
    target_id: str
    level: str
    entity_id: str
    loss: float
    trades: list[str]


def targets_from_limits(limits: list[Limit], valuation: pd.DataFrame) -> list[Target]:
    """One target per effective stress-loss limit: the limit's amount is the loss to reach."""
    out = []
    for lim in limits:
        if lim.limit_type is not LimitType.STRESS_LOSS:
            continue
        ids = list(trades_in_scope(lim, valuation))
        if ids:
            level = lim.scope.level.name
            out.append(Target(lim.limit_id, level, lim.scope.entity_id, float(lim.amount), ids))
    return out


# --- the quadratic problem -----------------------------------------------------------------------
@dataclass
class Problem:
    g: np.ndarray
    Q: np.ndarray
    B: np.ndarray  # J x k: sensitivity-factor moves (in bumps) per unit score
    lin: np.ndarray
    quad: np.ndarray
    M: np.ndarray | None = None
    """Rows for the price-like factors: their move per unit score, bounded below by PRICE_FLOOR."""

    def feasible(self, s: np.ndarray, slack: float = 1e-6) -> bool:
        return self.M is None or bool(np.all(self.M @ s >= PRICE_FLOOR - slack))

    def loss(self, s: np.ndarray) -> float:
        return float(-self.g @ s - s @ self.Q @ s)

    def grad(self, s: np.ndarray) -> np.ndarray:
        return -self.g - 2.0 * self.Q @ s


def problem(book: QuadBook, space: PCASpace, trades: list[str], relative: set[str] | None = None) -> Problem:
    lin, quad = book.scope(trades)
    a = book.A @ (space.sigma[:, None] * space.loadings)  # J x k
    B = np.asarray(a) / book.bump[:, None]  # noqa: N806
    move = space.sigma[:, None] * space.loadings  # F x k
    rel = [i for i, f in enumerate(space.factors) if f in (relative or ()) and space.sigma[i] > 0]
    return Problem(B.T @ lin, B.T @ (quad[:, None] * B), B, lin, quad, move[rel] if rel else None)


def hit(p: Problem, d: np.ndarray, target: float) -> float | None:
    """Smallest t > 0 with loss(t·d) = target along direction d (any length), or None."""
    a, b = -float(p.g @ d), float(d @ p.Q @ d)
    if abs(b) < 1e-14 * max(abs(a), 1e-300):
        return target / a if a > 0 else None
    disc = a * a - 4.0 * b * target
    if disc < 0:
        return None
    roots = [(a - math.sqrt(disc)) / (2 * b), (a + math.sqrt(disc)) / (2 * b)]
    pos = [r for r in roots if r > 0]
    return min(pos) if pos else None


def solve(
    p: Problem, target: float, prior: list[np.ndarray], min_angle: float = MIN_ANGLE_DEG
) -> np.ndarray | None:
    """The most plausible score vector losing at least ``target`` and pointing ``min_angle``
    degrees away from every ``prior`` solution, or None when it is out of reach."""
    d = -p.g.copy()
    for q in prior:  # start away from the solutions already found
        d -= (d @ q) / (q @ q) * q
    if np.linalg.norm(d) < 1e-12 * max(np.linalg.norm(p.g), 1e-300):
        vals, vecs = np.linalg.eigh(p.Q)
        d = vecs[:, 0]
        for q in prior:
            d = d - (d @ q) / (q @ q) * q
    if np.linalg.norm(d) < 1e-12:
        return None
    d = d / np.linalg.norm(d)
    t0 = hit(p, d, target)
    s0 = d * (t0 if t0 is not None else target / max(abs(p.g @ d), 1e-12))
    cos_max = math.cos(math.radians(min_angle))
    base = [
        {"type": "ineq", "fun": lambda s: p.loss(s) - target, "jac": lambda s: p.grad(s)},
        {"type": "ineq", "fun": lambda s: CAP**2 - s @ s, "jac": lambda s: -2.0 * s},
    ]
    for q in prior:
        qn = q / np.linalg.norm(q)
        base.append(
            {
                "type": "ineq",
                "fun": lambda s, qn=qn: cos_max * math.sqrt(s @ s + 1e-18) - s @ qn,
                "jac": lambda s, qn=qn: cos_max * s / math.sqrt(s @ s + 1e-18) - qn,
            }
        )
    active: list[int] = []  # price-like factors held above the floor (added as they are violated)
    s = s0
    for _ in range(BOUND_ROUNDS):
        cons = list(base)
        if active:
            rows = p.M[active]  # type: ignore[index]
            cons.append(
                {
                    "type": "ineq",
                    "fun": lambda s, rows=rows: rows @ s - PRICE_FLOOR,
                    "jac": lambda s, rows=rows: rows,
                }
            )
        res = minimize(
            lambda s: s @ s, s, jac=lambda s: 2.0 * s, constraints=cons, method="SLSQP",
            options={"maxiter": 300, "ftol": 1e-12},
        )  # fmt: skip
        s = res.x
        if not np.all(np.isfinite(s)):
            return None
        if p.M is None:
            break
        gap = p.M @ s - PRICE_FLOOR
        bad = [int(i) for i in np.argsort(gap)[:BOUND_BATCH] if gap[i] < -1e-6]
        if not bad:
            break
        active = sorted(set(active) | set(bad))
    if np.linalg.norm(s) > CAP * (1 + 1e-6) or not p.feasible(s, 1e-4):
        return None
    t = hit(p, s, target)  # land exactly on the target along the ray
    if t is None:
        return None
    s = s * t
    if p.loss(s) < target * (1 - 1e-6) or np.linalg.norm(s) > CAP * (1 + 1e-6) or not p.feasible(s, 1e-4):
        return None
    for q in prior:
        if s @ q / (np.linalg.norm(s) * np.linalg.norm(q)) > cos_max + 1e-6:
            return None
    return s


def neighbours(
    p: Problem, s: np.ndarray, target: float, n: int = NEIGHBOURS
) -> list[tuple[float, np.ndarray]]:
    """Rotations of ``s`` towards the directions of fastest-growing loss, back on the target."""
    u = s / np.linalg.norm(s)
    grad = p.grad(s)
    tangent = grad - (grad @ u) * u
    # the two leading directions of curvature of the loss on the tangent space
    proj = np.eye(len(s)) - np.outer(u, u)
    vals, vecs = np.linalg.eigh(proj @ (-p.Q) @ proj)
    cand = [tangent] if np.linalg.norm(tangent) > 1e-12 else []
    cand += [vecs[:, -1], vecs[:, -2] if len(vals) > 1 else vecs[:, -1]]
    dirs = []
    for c in cand:
        c = c - (c @ u) * u
        if np.linalg.norm(c) > 1e-12:
            dirs.append(c / np.linalg.norm(c))
    out: list[tuple[float, np.ndarray]] = []
    theta = math.radians(NEIGHBOUR_ANGLE_DEG)
    for c in dirs:
        for sign in (1.0, -1.0):
            d = math.cos(theta) * u + sign * math.sin(theta) * c
            t = hit(p, d, target)
            if t is not None and t * np.linalg.norm(d) <= CAP * 1.5 and p.feasible(d * t, 1e-4):
                out.append((NEIGHBOUR_ANGLE_DEG, d * t))
            if len(out) == n:
                return out
    return out


# --- confirmation --------------------------------------------------------------------------------
def _shocks(space: PCASpace, s: np.ndarray, scale: float, relative: set[str]) -> dict[str, float]:
    x = space.shocks(s)[0] * scale
    out = {}
    for f, v in zip(space.factors, x, strict=True):
        if v == 0.0 or not math.isfinite(v):
            continue
        out[f] = max(float(v), PRICE_FLOOR) if f in relative else float(v)
    return out


def full_loss(pf: Portfolio, trades: list[str], shocks: dict[str, float]) -> float:
    shocked = apply_shocks(pf.base, shocks, pf.universe)
    pv = pf.pv_under(shocked, trades)
    return -float(sum(pv[t] - pf.base_pv[t] for t in pv))


def confirm(
    pf: Portfolio, space: PCASpace, s: np.ndarray, trades: list[str], target: float, relative: set[str]
) -> tuple[float, float, float, bool]:
    """(loss at the search point, scale, loss at the scale, within tolerance). The scale moves the
    solution along its ray, bracketing then interpolating, until full revaluation loses the target
    (at most ``MAX_REVALUATIONS`` passes)."""
    lo: tuple[float, float] | None = None  # (scale, loss) below the target
    hi: tuple[float, float] | None = None  # above the target
    first: float | None = None
    best = (float("inf"), 1.0, 0.0)
    t = 1.0
    for _ in range(MAX_REVALUATIONS):
        loss = full_loss(pf, trades, _shocks(space, s, t, relative))
        first = loss if first is None else first
        if abs(loss - target) < best[0]:
            best = (abs(loss - target), t, loss)
        if best[0] <= CONFIRM_TOLERANCE * target:
            break
        if loss < target:
            lo = (t, loss) if lo is None or t > lo[0] else lo
        else:
            hi = (t, loss) if hi is None or t < hi[0] else hi
        if lo and hi:
            t = lo[0] + (target - lo[1]) * (hi[0] - lo[0]) / (hi[1] - lo[1])
        elif lo:
            t = lo[0] * float(np.clip(target / max(lo[1], 0.05 * target), 1.2, 4.0))
        elif hi:
            t = hi[0] * float(np.clip(target / hi[1], 0.25, 0.8))
        t = float(np.clip(t, *SCALE_BOUNDS))
    return float(first), best[1], best[2], best[0] <= CONFIRM_TOLERANCE * target


# --- run -----------------------------------------------------------------------------------------
@dataclass
class ReverseStressResult:
    solutions: pd.DataFrame
    neighbours: pd.DataFrame
    moves: pd.DataFrame
    space: dict[str, object] = field(default_factory=dict)
    tail: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {}
        if self.space:
            out["pca_components"] = int(self.space["components"])  # type: ignore[call-overload]
            out["pca_explained"] = float(self.space["explained"])  # type: ignore[arg-type]
            out["pca_tail_capture"] = float(self.space["tail_capture"])  # type: ignore[arg-type]
        if len(self.solutions):
            out["reverse_stress_targets"] = int(self.solutions["target_id"].nunique())
            first = self.solutions[
                (self.solutions["solution"] == 1) & (self.solutions["status"] != "UNREACHABLE")
            ]
            out["reverse_stress_unreachable"] = int((self.solutions["status"] == "UNREACHABLE").sum())
            if first.empty:
                return out
            firm = first[first["level"] == "FIRM"]
            if len(firm):
                r = firm.iloc[0]
                out.update(
                    reverse_firm_target=str(r["target_id"]),
                    reverse_firm_sigmas=float(r["refined_norm"]),
                    reverse_firm_probability=float(r["probability"]),
                )
            z = first.sort_values("refined_norm").iloc[0]
            out.update(
                reverse_easiest_target=str(z["target_id"]), reverse_easiest_sigmas=float(z["refined_norm"])
            )
        return out


def run_reverse_stress(
    pf: Portfolio,
    book: QuadBook,
    space: PCASpace,
    targets: list[Target],
    solutions: int = SOLUTIONS,
) -> ReverseStressResult:
    relative = {f for f, rf in pf.universe.items() if rf.shock_type != "ABSOLUTE"}
    hist_proj = space.scores(space._moves) if space._moves is not None else np.empty((0, space.k))
    sol_rows: list[dict[str, object]] = []
    nb_rows: list[dict[str, object]] = []
    move_rows: list[dict[str, object]] = []
    head = [f for f, _ in HEADLINE_FACTORS if f in space.factors]
    priced = set(pf.priced_ids)
    for tgt in targets:
        tgt.trades = [t for t in tgt.trades if t in priced]
        if tgt.loss <= 0 or not tgt.trades:
            continue
        p = problem(book, space, tgt.trades, relative)
        found: list[np.ndarray] = []
        for rank in range(1, solutions + 1):
            s = solve(p, tgt.loss, found)
            if s is None:
                if rank == 1:  # the book cannot lose this much within the move bounds
                    sol_rows.append(
                        {
                            c: {
                                "target_id": tgt.target_id,
                                "level": tgt.level,
                                "entity_id": tgt.entity_id,
                                "loss_target": tgt.loss,
                                "solution": 1,
                                "status": "UNREACHABLE",
                            }.get(c, float("nan"))
                            for c in COLUMNS
                        }
                    )
                break
            found.append(s)
            norm = float(np.linalg.norm(s))
            direction = s / norm
            nbs = neighbours(p, s, tgt.loss)
            ratios = [float(np.linalg.norm(v) / norm) for _, v in nbs]
            l_raw, scale, l_full, ok = confirm(pf, space, s, tgt.trades, tgt.loss, relative)
            angle = (
                math.degrees(math.acos(np.clip(s @ found[0] / (norm * np.linalg.norm(found[0])), -1, 1)))
                if rank > 1
                else 0.0
            )
            sigmas = norm * scale  # the confirmed scenario's distance, along its own direction
            sol_rows.append(
                {
                    "target_id": tgt.target_id,
                    "level": tgt.level,
                    "entity_id": tgt.entity_id,
                    "loss_target": tgt.loss,
                    "solution": rank,
                    "norm": norm,
                    "exceedance": float((hist_proj @ direction >= sigmas).mean())
                    if len(hist_proj)
                    else float("nan"),
                    "probability": float(normal.sf(sigmas)),
                    "angle_to_first": angle,
                    "taylor_loss": p.loss(s),
                    "full_loss_unscaled": l_raw,
                    "scale": scale,
                    "full_loss": l_full,
                    "refined_norm": norm * scale,
                    "status": "CONFIRMED" if ok else "UNCONFIRMED",
                    "neighbour_norm_ratio": float(np.median(ratios)) if ratios else float("nan"),
                }
            )
            for i, ((ang, v), r) in enumerate(zip(nbs, ratios, strict=True), 1):
                nb_rows.append(
                    {
                        "target_id": tgt.target_id,
                        "solution": rank,
                        "neighbour": i,
                        "angle": ang,
                        "norm": float(np.linalg.norm(v)),
                        "norm_ratio": r,
                    }
                )
            # What drives the loss: sensitivity factors by Taylor contribution, and the headline basket.
            y = p.B @ s
            contrib = p.lin * y + p.quad * y * y
            raw = book.A @ (space.shocks(s)[0])
            for j in np.argsort(contrib)[:DRIVERS]:
                fid, _ = book.rows[int(j)]
                move_rows.append(
                    {
                        "target_id": tgt.target_id,
                        "solution": rank,
                        "kind": "DRIVER",
                        "factor_id": fid,
                        "move": float(raw[int(j)]),
                        "pnl": float(contrib[int(j)]),
                    }
                )
            x = space.shocks(s)[0]
            for f in head:
                move_rows.append(
                    {
                        "target_id": tgt.target_id,
                        "solution": rank,
                        "kind": "HEADLINE",
                        "factor_id": f,
                        "move": float(x[space.factors.index(f)]),
                        "pnl": float("nan"),
                    }
                )
    return ReverseStressResult(
        pd.DataFrame(sol_rows, columns=COLUMNS),
        pd.DataFrame(nb_rows, columns=NEIGHBOUR_COLUMNS),
        pd.DataFrame(move_rows, columns=MOVE_COLUMNS),
        space.to_dict(),
        space.tail,
    )
