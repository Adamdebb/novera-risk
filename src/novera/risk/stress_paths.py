"""Stress paths with management actions, hedging and liquidation (MR-023, decision round 51).

A stress scenario is an instantaneous jump; a real stress unfolds over days, and the firm reacts.
This engine walks the book through a ``HORIZON``-day path of the whole market twice: once frozen
(nobody acts) and once managed (desks follow the owner's action rules), and reports what the
reaction saves or costs.

**Paths.** Three sources (framework part E):

- each LIMIT stress scenario in three shapes, ``GAP_DRIFT`` (60% on day one, the rest by the end),
  ``V`` (the full shock on day five, back to 40% by the end) and ``SLOW_BLEED`` (a straight line);
- the history's worst ``HORIZON``-day window for today's book, replayed day by day;
- the worst ``MC_TAIL`` of ``MC_PATHS`` daily fat-tailed paths in the stressed PCA space (MR-022).

**Each day** the book is revalued on its delta-gamma-vega model (MR-010) and earns its theta;
price-like factors cannot fall through ``PRICE_FLOOR``. The frozen book's end of path is confirmed
in full revaluation.

**Triggers.** A desk whose managed path loss reaches its stop-loss (a multiple of its standalone
1-day VaR) acts from the next day; when the firm's path loss reaches the firm's stress-loss limit,
every desk acts. Orders decided at a close execute at the next close, so the day in between hits the
old position.

**Actions** (each desk's rule, editable in Admin): cut every position by ``cut_fraction``, close
short option positions, and hedge the remaining delta each day with listed futures (rate futures
on the 2-year or 10-year point in the listed currencies, the regional equity index for single names,
the index itself, the FX pair, the commodity curve, the crypto future). A position trades at most
``participation`` × stressed volume a day (``stressed_volume_share`` of normal), so liquid positions
are cut first by construction; each trade pays the stressed half spread and square-root impact,

    cost = q·s/2·m + Y·σ_daily·√(q / V_stressed)·q

and the hedge is a position in the same path, so its basis to what it hedges is lost or gained.

Margin, collateral, funding and the liquidity spiral are round 52; redemptions are round 56.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

from novera.market_data.history import MarketHistory
from novera.risk import scenario_covariance as sc_cov
from novera.risk.pca_space import PCASpace, QuadBook
from novera.risk.revaluation import Portfolio
from novera.risk.reverse_stress import PRICE_FLOOR, Target

RECORD = "MR-023"
MODEL_VERSION = "1.0.0"

HORIZON = 20
SHAPES = ("GAP_DRIFT", "V", "SLOW_BLEED")
GAP = 0.6
V_BOTTOM_DAY = 5
V_END = 0.4
MC_PATHS = 2_000
MC_TAIL = 0.01
SCREEN_MULTIPLE = 5
WINDOW_CANDIDATES = 5
DEGREES_OF_FREEDOM = 4.0
SEED = 51
THETA_DAYS = 7.0 / 5.0
"""Calendar days of theta per business day (weekends included)."""
FIRM = "(firm)"
DEFAULT_SCOPE = "(default)"

LISTED_RATE_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF")
SHORT_END_YEARS = 3.0
RATE_FUTURE_DURATION = {"2Y": 1.9, "10Y": 8.5}
INDEX_BY_CURRENCY = {"USD": "SPX", "EUR": "SX5E", "CHF": "SX5E", "GBP": "FTSE", "JPY": "NKY"}
SHORT_OPTION_PRODUCTS = ("FX_OPTION", "EQUITY_OPTION", "EQUITY_EXOTIC", "COMMODITY_OPTION", "SWAPTION")
HEDGE_VOLUME = {"RATES": 20e9, "EQUITY_INDEX": 20e9, "FX": 20e9, "COMMODITY": 5e9, "CRYPTO": 1e9}
"""Normal daily notional traded in each listed hedge, reporting currency (assumption, MR-023)."""
HEDGE_SPREAD = {"RATES": 0.00005, "EQUITY_INDEX": 0.0001, "FX": 0.0001, "COMMODITY": 0.0003, "CRYPTO": 0.001}
"""Bid-ask spread of each listed hedge as a share of notional (MR-013 values for the futures)."""

SUMMARY_COLUMNS = [
    "path_id",
    "source",
    "scenario_id",
    "name",
    "shape",
    "book",
    "end_pnl",
    "max_drawdown",
    "worst_day",
    "worst_day_pnl",
    "first_breach_day",
    "first_breach_limit",
    "first_trigger_day",
    "desks_triggered",
    "completion_day",
    "unfinished_desks",
    "action_cost",
    "hedge_pnl",
    "taylor_end_pnl",
]
DAY_COLUMNS = ["path_id", "book", "day", "pnl", "cumulative", "hedge_pnl", "action_cost", "risk_held"]
DESK_COLUMNS = [
    "path_id",
    "desk_id",
    "book",
    "end_pnl",
    "max_drawdown",
    "stop_loss",
    "trigger_day",
    "completion_day",
    "action_cost",
    "hedge_pnl",
]
ACTION_COLUMNS = ["path_id", "desk_id", "action", "first_day", "last_day", "traded", "cost"]


# --- the owner's rules ----------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionRule:
    """What a desk does in a stress path. ``scope`` is ``DEFAULT_SCOPE`` or a desk id; a desk's own
    row wins over the default."""

    scope: str = DEFAULT_SCOPE
    enabled: bool = True
    stop_loss_var_multiple: float = 3.0
    cut_fraction: float = 0.5
    close_short_options: bool = True
    hedge_delta: bool = True
    participation: float = 0.20
    stressed_volume_share: float = 0.5
    stressed_spread_multiple: float = 2.0
    impact_coefficient: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionRule:
        names = {f.name for f in fields(cls)}
        unknown = sorted(set(d) - names)
        if unknown:
            raise ValueError(f"unknown action rule fields: {', '.join(unknown)}")
        return cls(**d)

    def problems(self) -> list[str]:
        out = []
        if self.stop_loss_var_multiple <= 0:
            out.append(f"{self.scope}: the stop-loss multiple must be positive")
        if not 0.0 <= self.cut_fraction <= 1.0:
            out.append(f"{self.scope}: the cut fraction lies between 0 and 1")
        if not 0.0 < self.participation <= 1.0:
            out.append(f"{self.scope}: participation lies in (0, 1]")
        if not 0.0 < self.stressed_volume_share <= 1.0:
            out.append(f"{self.scope}: the stressed volume share lies in (0, 1]")
        if self.stressed_spread_multiple < 1.0:
            out.append(f"{self.scope}: the stressed spread multiple is at least 1")
        if self.impact_coefficient < 0.0:
            out.append(f"{self.scope}: the impact coefficient cannot be negative")
        return out


DEFAULT_RULES = (ActionRule(),)


def validate_rules(rules: list[ActionRule], desks: list[str] | None = None) -> list[str]:
    """Problems with a rule set: exactly one default row, no desk twice, known desks, sane values."""
    out: list[str] = []
    scopes = [r.scope for r in rules]
    if scopes.count(DEFAULT_SCOPE) != 1:
        out.append(f"the rule set needs exactly one {DEFAULT_SCOPE} row")
    dup = sorted({s for s in scopes if scopes.count(s) > 1 and s != DEFAULT_SCOPE})
    if dup:
        out.append(f"desks listed twice: {', '.join(dup)}")
    if desks is not None:
        unknown = sorted(set(scopes) - set(desks) - {DEFAULT_SCOPE})
        if unknown:
            out.append(f"unknown desks: {', '.join(unknown)}")
    for r in rules:
        out += r.problems()
    return out


def rules_by_desk(rules: list[ActionRule], desks: list[str]) -> dict[str, ActionRule]:
    by = {r.scope: r for r in rules}
    default = by.get(DEFAULT_SCOPE, ActionRule())
    return {d: by.get(d, default) for d in desks}


# --- paths ----------------------------------------------------------------------------------------
@dataclass
class StressPath:
    path_id: str
    source: str  # SCENARIO, HISTORICAL, MONTE_CARLO
    scenario_id: str | None
    name: str
    shape: str | None
    moves: np.ndarray
    """(HORIZON + 1) x factors: the cumulative move of every factor since today, in shock units."""


def shape_weights(shape: str, horizon: int = HORIZON) -> np.ndarray:
    t = np.arange(horizon + 1, dtype=float)
    if shape == "GAP_DRIFT":
        w = GAP + (1.0 - GAP) * (t - 1.0) / max(horizon - 1, 1)
        w[0] = 0.0
    elif shape == "V":
        bottom = min(V_BOTTOM_DAY, horizon)
        w = np.where(t <= bottom, t / bottom, 1.0 - (1.0 - V_END) * (t - bottom) / max(horizon - bottom, 1))
    elif shape == "SLOW_BLEED":
        w = t / horizon
    else:
        raise ValueError(f"unknown path shape {shape}")
    return w


def _daily_scale(space: PCASpace) -> np.ndarray:
    """Factor moves per unit daily score: the stressed horizon loadings over √horizon."""
    return space.loadings * (space.sigma / math.sqrt(space.horizon))[:, None]


def _floor(x: np.ndarray, relative: np.ndarray) -> np.ndarray:
    x[..., relative] = np.maximum(x[..., relative], PRICE_FLOOR)
    return x


def _compound(x: np.ndarray, relative: np.ndarray) -> np.ndarray:
    """Summed daily moves to shocks: price-like factors compound as log returns (a run of daily falls
    cannot pass −100%, and rises and falls are not symmetric in price), then the floor."""
    x[..., relative] = np.expm1(x[..., relative])
    return _floor(x, relative)


def scenario_paths(
    results: list[Any], space: PCASpace, relative: np.ndarray, horizon: int = HORIZON
) -> list[StressPath]:
    """Each stress result (``StressResult``) in every shape. The shapes carry no noise: a bridge of
    stressed daily noise moved the bank by more than most scenarios lose (decision 51.5)."""
    pos = {f: i for i, f in enumerate(space.factors)}
    out = []
    for r in results:
        end = np.zeros(len(space.factors))
        for f, v in r.shocks.items():
            if f in pos:
                end[pos[f]] = v
        for shape in SHAPES:
            w = shape_weights(shape, horizon)
            x = _floor(w[:, None] * end[None, :], relative)
            out.append(
                StressPath(
                    f"{r.scenario.scenario_id}:{shape}",
                    "SCENARIO",
                    r.scenario.scenario_id,
                    r.scenario.name,
                    shape,
                    x,
                )
            )
    return out


def historical_path(
    history: MarketHistory,
    as_of: date,
    pf: Portfolio,
    book: QuadBook,
    firm: tuple[np.ndarray, np.ndarray],
    horizon: int = HORIZON,
    workers: int | None = None,
) -> StressPath | None:
    """The history's ``horizon``-day window that hurts today's book most (screened on the
    sensitivities, chosen in full revaluation), replayed day by day from its first close."""
    try:
        ms = sc_cov.move_set(history, as_of, horizon, pf.universe)
    except ValueError:
        return None
    factors = book.factors
    x = np.zeros((len(ms.dates), len(factors)))
    present = [i for i, f in enumerate(factors) if f in ms.index]
    x[:, present] = np.nan_to_num(ms.moves[:, ms.cols([factors[i] for i in present])])
    pnl = book.pnl(x, *firm)
    cand = np.argsort(pnl)[:WINDOW_CANDIDATES]
    full = _full_pnl(pf, factors, x[cand], workers).sum(axis=1)
    end = ms.dates[int(cand[int(np.argmin(full))])]
    w = history.window(end, horizon + 1)
    cols = [f for f in factors if f in w.columns]
    vals = w[cols].to_numpy(dtype=float)
    uni = pf.universe or {}
    is_abs = np.array([f not in uni or uni[f].shock_type == "ABSOLUTE" for f in cols])
    with np.errstate(divide="ignore", invalid="ignore"):
        cum = np.where(is_abs, vals - vals[0], vals / vals[0] - 1.0)
    cum = np.nan_to_num(cum, nan=0.0, posinf=0.0, neginf=0.0)
    moves = np.zeros((horizon + 1, len(factors)))
    fpos = {f: i for i, f in enumerate(factors)}
    moves[:, [fpos[f] for f in cols]] = cum
    start = w.index[0]
    return StressPath(
        f"HISTORICAL:{start}",
        "HISTORICAL",
        None,
        f"Worst {horizon}-day window, {start} to {end}",
        None,
        moves,
    )


def _full_pnl(pf: Portfolio, factors: list[str], rows: np.ndarray, workers: int | None) -> np.ndarray:
    """Full-revaluation P&L per row of factor moves (rows x trades, in ``pf.priced_ids`` order)."""
    frame = pd.DataFrame(rows, columns=factors)
    frame = frame.loc[:, (frame != 0).any(axis=0)]
    return pf.pnl_matrix(frame, workers=workers).to_numpy()


def monte_carlo_tail(
    pf: Portfolio,
    space: PCASpace,
    book: QuadBook,
    firm: tuple[np.ndarray, np.ndarray],
    relative: np.ndarray,
    horizon: int = HORIZON,
    paths: int = MC_PATHS,
    tail: float = MC_TAIL,
    df: float = DEGREES_OF_FREEDOM,
    seed: int = SEED,
    workers: int | None = None,
) -> list[StressPath]:
    """Daily fat-tailed paths (Student-t scores with one scale per path, MR-022); the ``tail`` share
    that ends worst for the firm, worst first. The sensitivities screen ``SCREEN_MULTIPLE`` times as
    many; full revaluation of their end points chooses (option books make the screen unreliable)."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((paths, horizon, space.k))
    scale = np.sqrt((df - 2.0) / rng.chisquare(df, size=paths))
    cum = np.cumsum(z, axis=1) * scale[:, None, None]
    daily = _daily_scale(space)
    end = _compound(cum[:, -1, :] @ daily.T, relative)
    n = max(int(round(tail * paths)), 1)
    screened = np.argsort(book.pnl(end, *firm))[: min(SCREEN_MULTIPLE * n, paths)]
    full = _full_pnl(pf, book.factors, end[screened], workers).sum(axis=1)
    worst = screened[np.argsort(full, kind="stable")[:n]]
    out = []
    for rank, i in enumerate(worst, 1):
        x = np.vstack([np.zeros(len(space.factors)), cum[i] @ daily.T])
        out.append(
            StressPath(
                f"MONTE_CARLO:{rank:02d}",
                "MONTE_CARLO",
                None,
                f"Fat-tailed path {rank} of {n} (of {paths})",
                None,
                _compound(x, relative),
            )
        )
    return out


# --- hedges ---------------------------------------------------------------------------------------
@dataclass
class Hedges:
    """Listed hedge instruments: their moves are ``G @ x / bump``; ``R`` maps sensitivity rows to
    the instrument that hedges them."""

    names: list[str]
    kinds: list[str]
    G: sparse.csr_matrix  # instruments x factors
    bump: np.ndarray
    conv: np.ndarray  # P&L per bump per unit notional
    sigma: np.ndarray  # daily standard deviation of the move, in bumps
    R: sparse.csr_matrix  # rows x instruments


def _hedge_of(fid: str, pf: Portfolio, fpos: dict[str, int]) -> tuple[str, str, str | None] | None:
    """(instrument name, kind, factor) hedging a delta row; factor None means the row itself."""
    parts = fid.split(":")
    prefix = parts[0]
    uni = pf.universe or {}
    if prefix == "IR":
        ccy = parts[1]
        if ccy not in LISTED_RATE_CURRENCIES:
            return None
        rf = uni.get(fid)
        years = rf.tenor_years if rf is not None and rf.tenor_years is not None else 10.0
        point = "2Y" if years <= SHORT_END_YEARS else "10Y"
        ref = f"IR:{ccy}:{point}"
        return (f"{ccy} {point} rate future", "RATES", ref) if ref in fpos else None
    if prefix == "EQ":
        rf = uni.get(fid)
        idx = INDEX_BY_CURRENCY.get(rf.currency if rf is not None else "")
        ref = f"EQIDX:{idx}" if idx else ""
        return (f"{idx} index future", "EQUITY_INDEX", ref) if ref in fpos else None
    if prefix == "EQIDX":
        return (f"{parts[1]} index future", "EQUITY_INDEX", fid) if fid in fpos else None
    if prefix == "FX":
        return (f"{parts[1]} FX future", "FX", fid) if fid in fpos else None
    if prefix == "CMD":
        return (f"{parts[1]} future", "COMMODITY", None)
    if prefix == "CRYPTO":
        return (f"{parts[1]} future", "CRYPTO", fid) if fid in fpos else None
    return None


def build_hedges(pf: Portfolio, book: QuadBook, space: PCASpace) -> Hedges:
    fpos = {f: i for i, f in enumerate(book.factors)}
    names: list[str] = []
    kinds: list[str] = []
    g_rows: list[sparse.csr_matrix] = []
    bumps: list[float] = []
    conv: list[float] = []
    r_r, r_c = [], []
    for j, (fid, bump) in enumerate(book.rows):
        if fid.startswith(("VOL:", "SWVOL:")):
            continue  # a vega row shares its prefix with nothing hedged here
        h = _hedge_of(fid, pf, fpos)
        if h is None:
            continue
        name, kind, ref = h
        if name not in names:
            names.append(name)
            kinds.append(kind)
            if ref is None:
                g_rows.append(book.A[j])
            else:
                g_rows.append(sparse.csr_matrix(([1.0], ([0], [fpos[ref]])), shape=(1, len(book.factors))))
            bumps.append(bump)
            point = name.split()[1] if kind == "RATES" else ""
            conv.append(RATE_FUTURE_DURATION.get(point, 8.5) * bump if kind == "RATES" else bump)
        r_r.append(j)
        r_c.append(names.index(name))
    nb = len(names)
    G = sparse.vstack(g_rows).tocsr() if g_rows else sparse.csr_matrix((0, len(book.factors)))  # noqa: N806
    b = np.array(bumps)
    daily = _daily_scale(space)
    sig = np.sqrt(np.asarray((G @ daily) ** 2).sum(axis=1)).ravel() / b if nb else np.zeros(0)
    R = sparse.csr_matrix((np.ones(len(r_r)), (r_r, r_c)), shape=(len(book.rows), nb))  # noqa: N806
    return Hedges(names, kinds, G, b, np.array(conv), sig, R)


# --- the engine -----------------------------------------------------------------------------------
@dataclass
class _Outcome:
    summary: list[dict[str, Any]]
    days: list[dict[str, Any]]
    desks: list[dict[str, Any]]
    actions: list[dict[str, Any]]


def _drawdown(cum: np.ndarray) -> float:
    c = np.concatenate([[0.0], cum])
    return float((c - np.maximum.accumulate(c)).min())


class PathEngine:
    """The book and the rules, prepared once; ``run`` walks one path frozen and managed."""

    def __init__(
        self,
        pf: Portfolio,
        book: QuadBook,
        space: PCASpace,
        sens: pd.DataFrame,
        valuation: pd.DataFrame,
        liquidity_by_trade: pd.DataFrame,
        desk_var: dict[str, float],
        targets: list[Target],
        rules: list[ActionRule],
    ) -> None:
        self.book, self.space = book, space
        trades = book.trades
        v = valuation.drop_duplicates("trade_id").set_index("trade_id")
        desk = v["desk_id"].reindex(trades).fillna("(no desk)").astype(str)
        self.desks = sorted(desk.unique())
        dpos = {d: i for i, d in enumerate(self.desks)}
        di = desk.map(dpos).to_numpy()
        n, nd = len(trades), len(self.desks)
        self.Dm = sparse.csr_matrix((np.ones(n), (di, np.arange(n))), shape=(nd, n))
        self.desk_index = di
        self.L, self.Q = book.lin.tocsr(), book.quad.tocsr()
        th = sens[sens["measure"] == "THETA"].groupby("trade_id")["value"].sum()
        self.theta = th.reindex(trades).fillna(0.0).to_numpy() * THETA_DAYS
        bmat = np.asarray(book.A @ _daily_scale(space)) / book.bump[:, None]
        self.loading = np.asarray(self.L @ bmat)  # trades x k: daily P&L per unit daily score
        self.std = np.sqrt((self.loading**2).sum(axis=1))
        liq = liquidity_by_trade.drop_duplicates("trade_id").set_index("trade_id")
        self.adv = liq["adv"].reindex(trades).fillna(np.inf).to_numpy(dtype=float)
        self.position = liq["position"].reindex(trades).fillna(0.0).to_numpy(dtype=float)
        self.half_spread = liq["bidask_cost"].reindex(trades).fillna(0.0).to_numpy(dtype=float)
        prod = v["product_type"].reindex(trades).astype(str)
        dirn = v["direction"].reindex(trades).astype(str)
        self.short_option = (prod.isin(SHORT_OPTION_PRODUCTS) & (dirn == "SELL")).to_numpy()
        self.rules = rules_by_desk(rules, self.desks)
        r = [self.rules[d] for d in self.desks]
        self.enabled = np.array([x.enabled for x in r])
        self.stop = np.array(
            [
                x.stop_loss_var_multiple * desk_var[d] if desk_var.get(d, 0.0) > 0 else np.inf
                for x, d in zip(r, self.desks, strict=True)
            ]
        )
        self.var = np.array([desk_var.get(d, 0.0) for d in self.desks])
        per_trade = lambda attr: np.array([getattr(r[i], attr) for i in di], dtype=float)  # noqa: E731
        self.cut = per_trade("cut_fraction")
        self.close_short = per_trade("close_short_options").astype(bool)
        self.part = per_trade("participation")
        self.svs = per_trade("stressed_volume_share")
        self.mult = per_trade("stressed_spread_multiple")
        self.impact = per_trade("impact_coefficient")
        with np.errstate(divide="ignore", invalid="ignore"):
            cap = self.part * self.svs * self.adv / self.position
        self.cap = np.where(self.position > 0, cap, np.inf)
        self.hedges = build_hedges(pf, book, space)
        hd = lambda attr: np.array([getattr(x, attr) for x in r], dtype=float)  # noqa: E731
        self.hedge_on = np.array([x.hedge_delta for x in r])
        self.d_part, self.d_svs = hd("participation"), hd("stressed_volume_share")
        self.d_mult, self.d_impact = hd("stressed_spread_multiple"), hd("impact_coefficient")
        self.LR = (self.L @ self.hedges.R).tocsr()
        # Limits: which trades and which desks' hedges each stress-loss limit reads.
        tpos = {t: i for i, t in enumerate(trades)}
        self.targets = targets
        tm = np.zeros((len(targets), n))
        td = np.zeros((len(targets), nd))
        for k, t in enumerate(targets):
            idx = [tpos[x] for x in t.trades if x in tpos]
            tm[k, idx] = 1.0
            inside = np.bincount(di[idx], minlength=nd) if idx else np.zeros(nd)
            total = np.bincount(di, minlength=nd)
            td[k] = (total > 0) & (inside == total)
        self.Tm, self.Td = tm, td
        firm = [t for t in targets if t.level == "FIRM"]
        self.firm_limit = float(firm[0].loss) if firm else np.inf
        self.firm_limit_id = firm[0].target_id if firm else None

    # -- one path ----------------------------------------------------------------------------------
    def run(self, path: StressPath, full: np.ndarray) -> _Outcome:
        """``full``: each trade's full-revaluation P&L at each day's close against today (H x trades)."""
        book, hz = self.book, path.moves.shape[0] - 1
        y = book.moves(path.moves)  # (H+1) x J, for the hedge ratios
        dv = np.diff(np.vstack([np.zeros(full.shape[1]), full]), axis=0).T  # trades x H
        hg = self.hedges
        yb = (np.asarray(hg.G @ path.moves.T).T / hg.bump) if len(hg.names) else np.zeros((hz + 1, 0))
        dyb = np.diff(yb, axis=0)
        n, nd, nb = len(self.theta), len(self.desks), len(hg.names)

        # Frozen.
        fr_tr = dv + self.theta[:, None]  # trades x H
        fr_day = fr_tr.sum(axis=0)
        fr_cum = np.cumsum(fr_day)
        fr_desk_cum = np.cumsum(np.asarray(self.Dm @ fr_tr), axis=1)
        fr_tgt_cum = np.cumsum(self.Tm @ fr_tr, axis=1) if len(self.targets) else np.zeros((0, hz))
        fr_trig = [
            next((t + 1 for t in range(hz) if fr_desk_cum[d, t] <= -self.stop[d]), None) for d in range(nd)
        ]

        # Managed.
        h = np.ones(n)
        target_h = np.ones(n)
        hedge = np.zeros((nd, nb))
        trig_day: list[int | None] = [None] * nd
        done_day: list[int | None] = [None] * nd
        triggered = np.zeros(nd, dtype=bool)
        mg_day = np.zeros(hz)
        hedge_day = np.zeros(hz)
        cost_day = np.zeros(hz)
        held = np.zeros(hz)
        desk_cum = np.zeros(nd)
        desk_hedge = np.zeros(nd)
        desk_cost = np.zeros(nd)
        desk_path = np.zeros((nd, hz))
        desk_hcost = np.zeros(nd)
        tgt_path = np.zeros((len(self.targets), hz))
        cum_tr = np.zeros(n)
        acts: dict[tuple[str, str], dict[str, Any]] = {}
        risk0 = float(self.std.sum()) or 1.0
        firm_cum = 0.0

        def act(desk: int, action: str, day: int, traded: float, cost: float) -> None:
            k = (self.desks[desk], action)
            a = acts.setdefault(k, {"first_day": day, "last_day": day, "traded": 0.0, "cost": 0.0})
            a["last_day"], a["traded"], a["cost"] = day, a["traded"] + traded, a["cost"] + cost

        for t in range(hz):
            day = t + 1
            tr = h * fr_tr[:, t]
            hp = hedge @ dyb[t] if nb else np.zeros(nd)
            # Orders decided at yesterday's close execute at today's close.
            cost_tr = np.zeros(n)
            act_mask = triggered[self.desk_index] & self.enabled[self.desk_index]
            if act_mask.any():
                dh = np.where(act_mask, np.clip(h - target_h, 0.0, self.cap), 0.0)
                if dh.any():
                    with np.errstate(divide="ignore", invalid="ignore"):
                        qv = np.where(np.isfinite(self.cap) & (self.cap > 0), dh * self.part / self.cap, 0.0)
                    # Spread on every trade; impact on the desk's net risk sold (offsetting trades net).
                    cost_tr = dh * self.half_spread * self.mult
                    for d in np.unique(self.desk_index[dh > 0]):
                        m = (self.desk_index == d) & (dh > 0)
                        w = dh[m] * self.std[m] * np.sqrt(qv[m])
                        net = float(np.linalg.norm(dh[m] @ self.loading[m]))
                        gross = float((dh[m] * self.std[m]).sum())
                        if w.sum() > 0 and gross > 0:
                            cost_tr[m] += self.impact[m] * net * w / gross
                        short = m & self.short_option & self.close_short
                        cut = m & ~short
                        if cut.any():
                            traded = float((dh[cut] * self.std[cut]).sum())
                            act(d, "CUT", day, traded, float(cost_tr[cut].sum()))
                        if short.any():
                            act(
                                d,
                                "CLOSE_SHORT_OPTIONS",
                                day,
                                float((dh[short] * self.std[short]).sum()),
                                float(cost_tr[short].sum()),
                            )
                    h = h - dh
            hcost = np.zeros(nd)
            if nb and (triggered & self.hedge_on & self.enabled).any():
                yt = y[t + 1]
                delta_rows = self.LR + (self.Q.multiply(2.0 * yt[None, :]).tocsr() @ hg.R)
                exposure = np.asarray(self.Dm @ delta_rows.multiply(h[:, None]).tocsr().toarray())
                for d in np.flatnonzero(triggered & self.hedge_on & self.enabled):
                    want = -exposure[d]
                    cap_units = (
                        self.d_part[d]
                        * self.d_svs[d]
                        * np.array([HEDGE_VOLUME[k] for k in hg.kinds])
                        * hg.conv
                    )
                    dH = np.clip(want - hedge[d], -cap_units, cap_units)  # noqa: N806
                    notional = np.abs(dH) / hg.conv
                    qv = notional / (self.d_svs[d] * np.array([HEDGE_VOLUME[k] for k in hg.kinds]))
                    c = notional * np.array([HEDGE_SPREAD[k] for k in hg.kinds]) * 0.5 * self.d_mult[d] + (
                        self.d_impact[d] * np.sqrt(qv) * np.abs(dH) * hg.sigma
                    )
                    hedge[d] += dH
                    hcost[d] = float(c.sum())
                    if notional.sum() > 0:
                        act(d, "HEDGE", day, float(notional.sum()), hcost[d])
            net_tr = tr - cost_tr
            desk_day = np.asarray(self.Dm @ net_tr).ravel() + hp - hcost
            desk_cum += desk_day
            desk_path[:, t] = desk_cum
            desk_hedge += hp
            desk_cost += np.asarray(self.Dm @ cost_tr).ravel() + hcost
            cum_tr += net_tr
            desk_hcost += hcost
            if len(self.targets):
                tgt_path[:, t] = self.Tm @ cum_tr + self.Td @ (desk_hedge - desk_hcost)
            mg_day[t] = float(desk_day.sum())
            hedge_day[t] = float(hp.sum())
            cost_day[t] = float(cost_tr.sum() + hcost.sum())
            held[t] = float((h * self.std).sum()) / risk0
            firm_cum += mg_day[t]
            # Completion: a triggered desk is done when every position sits at its target.
            for d in np.flatnonzero(triggered):
                if done_day[d] is None:
                    m = self.desk_index == d
                    if np.all(h[m] <= target_h[m] + 1e-9):
                        done_day[d] = day
            # Triggers at today's close: tomorrow's orders.
            new = (~triggered) & self.enabled & (desk_cum <= -self.stop)
            if firm_cum <= -self.firm_limit:
                new |= (~triggered) & self.enabled
            for d in np.flatnonzero(new):
                triggered[d] = True
                trig_day[d] = day
                m = self.desk_index == d
                target_h[m] = np.minimum(target_h[m], 1.0 - self.cut[m])
                target_h[m & self.short_option & self.close_short] = 0.0

        mg_cum = np.cumsum(mg_day)

        def first_breach(cum: np.ndarray) -> tuple[int | None, str | None]:
            best: tuple[int | None, str | None] = (None, None)
            for k, tg in enumerate(self.targets):
                hit = np.flatnonzero(cum[k] <= -tg.loss)
                if len(hit) and (best[0] is None or hit[0] + 1 < best[0]):
                    best = (int(hit[0]) + 1, tg.target_id)
            return best

        base = {
            "path_id": path.path_id,
            "source": path.source,
            "scenario_id": path.scenario_id,
            "name": path.name,
            "shape": path.shape,
        }
        fb = first_breach(fr_tgt_cum)
        mb = first_breach(tgt_path)
        trig_any = [d for d in trig_day if d is not None]
        fr_trig_any = [d for d in fr_trig if d is not None]
        unfinished = [self.desks[d] for d in range(nd) if trig_day[d] is not None and done_day[d] is None]
        finished = [done_day[d] for d in range(nd) if trig_day[d] is not None and done_day[d] is not None]
        summary = [
            {
                **base,
                "book": "FROZEN",
                "end_pnl": float(fr_cum[-1]),
                "max_drawdown": _drawdown(fr_cum),
                "worst_day": int(np.argmin(fr_day)) + 1,
                "worst_day_pnl": float(fr_day.min()),
                "first_breach_day": fb[0],
                "first_breach_limit": fb[1],
                "first_trigger_day": min(fr_trig_any) if fr_trig_any else None,
                "desks_triggered": len(fr_trig_any),
                "completion_day": None,
                "unfinished_desks": None,
                "action_cost": 0.0,
                "hedge_pnl": 0.0,
                "taylor_end_pnl": None,
            },
            {
                **base,
                "book": "MANAGED",
                "end_pnl": float(mg_cum[-1]),
                "max_drawdown": _drawdown(mg_cum),
                "worst_day": int(np.argmin(mg_day)) + 1,
                "worst_day_pnl": float(mg_day.min()),
                "first_breach_day": mb[0],
                "first_breach_limit": mb[1],
                "first_trigger_day": min(trig_any) if trig_any else None,
                "desks_triggered": len(trig_any),
                "completion_day": (max(finished) if finished and not unfinished else None),
                "unfinished_desks": ", ".join(unfinished) if unfinished else None,
                "action_cost": float(cost_day.sum()),
                "hedge_pnl": float(hedge_day.sum()),
                "taylor_end_pnl": None,
            },
        ]
        days = []
        for t in range(hz):
            days.append(
                {
                    "path_id": path.path_id,
                    "book": "FROZEN",
                    "day": t + 1,
                    "pnl": float(fr_day[t]),
                    "cumulative": float(fr_cum[t]),
                    "hedge_pnl": 0.0,
                    "action_cost": 0.0,
                    "risk_held": 1.0,
                }
            )
            days.append(
                {
                    "path_id": path.path_id,
                    "book": "MANAGED",
                    "day": t + 1,
                    "pnl": float(mg_day[t]),
                    "cumulative": float(mg_cum[t]),
                    "hedge_pnl": float(hedge_day[t]),
                    "action_cost": float(cost_day[t]),
                    "risk_held": float(held[t]),
                }
            )
        desks = []
        for d, name in enumerate(self.desks):
            desks.append(
                {
                    "path_id": path.path_id,
                    "desk_id": name,
                    "book": "FROZEN",
                    "end_pnl": float(fr_desk_cum[d, -1]),
                    "max_drawdown": _drawdown(fr_desk_cum[d]),
                    "stop_loss": float(self.stop[d]) if np.isfinite(self.stop[d]) else None,
                    "trigger_day": fr_trig[d],
                    "completion_day": None,
                    "action_cost": 0.0,
                    "hedge_pnl": 0.0,
                }
            )
            desks.append(
                {
                    "path_id": path.path_id,
                    "desk_id": name,
                    "book": "MANAGED",
                    "end_pnl": float(desk_path[d, -1]),
                    "max_drawdown": _drawdown(desk_path[d]),
                    "stop_loss": float(self.stop[d]) if np.isfinite(self.stop[d]) else None,
                    "trigger_day": trig_day[d],
                    "completion_day": done_day[d],
                    "action_cost": float(desk_cost[d]),
                    "hedge_pnl": float(desk_hedge[d]),
                }
            )
        actions = [{"path_id": path.path_id, "desk_id": k[0], "action": k[1], **a} for k, a in acts.items()]
        return _Outcome(summary, days, desks, actions)


# --- the stage ------------------------------------------------------------------------------------
@dataclass
class StressPathsResult:
    summary_table: pd.DataFrame
    """One row per path and book (FROZEN, MANAGED)."""
    days: pd.DataFrame
    desks: pd.DataFrame
    actions: pd.DataFrame
    parameters: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        s = self.summary_table
        if s.empty:
            return {"stress_paths": 0}
        fr = s[s["book"] == "FROZEN"].set_index("path_id")
        mg = s[s["book"] == "MANAGED"].set_index("path_id")
        worst = str(fr["max_drawdown"].idxmin())
        breach = fr["first_breach_day"].dropna()
        return {
            "stress_paths": int(len(fr)),
            "path_worst_id": worst,
            "path_worst_drawdown_frozen": float(fr.loc[worst, "max_drawdown"]),
            "path_worst_drawdown_managed": float(mg.loc[worst, "max_drawdown"]),
            "path_worst_action_cost": float(mg.loc[worst, "action_cost"]),
            "path_first_breach_day": int(breach.min()) if len(breach) else None,
            "path_unfinished": int(mg["unfinished_desks"].notna().sum()),
        }


def run_stress_paths(
    pf: Portfolio,
    book: QuadBook,
    space: PCASpace,
    sens: pd.DataFrame,
    history: MarketHistory,
    valuation: pd.DataFrame,
    liquidity_by_trade: pd.DataFrame,
    desk_var: dict[str, float],
    targets: list[Target],
    limit_results: list[Any],
    rules: list[ActionRule],
    horizon: int = HORIZON,
    workers: int | None = None,
) -> StressPathsResult:
    """Every path of the three sources, revalued in full at every close, walked frozen and managed.
    The sensitivity approximation of each frozen end is kept beside it for information."""
    uni = pf.universe or {}
    relative = np.array([f in uni and uni[f].shock_type != "ABSOLUTE" for f in book.factors])
    firm = book.scope(list(book.trades))
    paths = scenario_paths(limit_results, space, relative, horizon)
    hist = historical_path(history, pf.as_of, pf, book, firm, horizon, workers)
    if hist is not None:
        paths.append(hist)
    paths += monte_carlo_tail(pf, space, book, firm, relative, horizon, workers=workers)
    engine = PathEngine(pf, book, space, sens, valuation, liquidity_by_trade, desk_var, targets, rules)
    rows = np.vstack([p.moves[1:] for p in paths]) if paths else np.zeros((0, len(book.factors)))
    full = _full_pnl(pf, book.factors, rows, workers) if len(rows) else np.zeros((0, len(book.trades)))
    theta_total = float(engine.theta.sum()) * horizon
    summary, days, desks, actions = [], [], [], []
    for k, p in enumerate(paths):
        o = engine.run(p, full[k * horizon : (k + 1) * horizon])
        o.summary[0]["taylor_end_pnl"] = float(book.pnl(p.moves[-1], *firm)[0]) + theta_total
        summary += o.summary
        days += o.days
        desks += o.desks
        actions += o.actions
    params = {
        "horizon_days": horizon,
        "shapes": ", ".join(SHAPES),
        "scenario_paths": len(limit_results) * len(SHAPES),
        "historical_paths": int(hist is not None),
        "monte_carlo_paths": MC_PATHS,
        "monte_carlo_tail_paths": max(int(round(MC_TAIL * MC_PATHS)), 1),
        "degrees_of_freedom": DEGREES_OF_FREEDOM,
        "seed": SEED,
        "components": space.k,
        "regime": space.regime,
        "hedge_instruments": len(engine.hedges.names),
        "firm_limit": engine.firm_limit if np.isfinite(engine.firm_limit) else None,
        "rules": [r.to_dict() for r in rules],
    }
    return StressPathsResult(
        pd.DataFrame(summary, columns=SUMMARY_COLUMNS),
        pd.DataFrame(days, columns=DAY_COLUMNS),
        pd.DataFrame(desks, columns=DESK_COLUMNS),
        pd.DataFrame(actions, columns=ACTION_COLUMNS),
        params,
    )
