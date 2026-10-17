"""Margin, collateral and funding chain with the liquidity spiral (MR-024, decision round 52).

A stress loss hurts twice: once as P&L and once as cash. This engine follows the cash on the stress
paths of MR-023: what the firm must pay at each close, what it can still raise, and how many days it
lasts. Each path is walked, frozen and managed, under three funding scenarios (normal, firm-specific
and market-wide, owner's rules in Admin).

**Where a trade's P&L becomes cash** (its margin regime):

- ``VM``: cleared at LCH or ICE, a listed future, a listed option sold, or a security sold short (the
  lender calls collateral every day). Each day's P&L is paid or received the same day.
- ``CSA``: a bilateral netting set with a credit support annex. Collateral follows the set's value
  beyond each side's threshold; the collateral we post is securities at the CSA haircut.
- ``FUNDED``: a security we own (bond, share, fund, coin). P&L moves an asset, not cash; the
  government bonds among them are the bank's high-quality liquid assets (HQLA).
- ``UNMARGINED``: everything else (bilateral without a CSA, listed options bought, FX spot, repo).

**Initial margin** we post moves with the netting set's risk at the stressed point: SIMM-lite of
today (REG-004) times σ_t/σ_0, where σ is the norm of the set's daily P&L loadings on the PCA scores
with each trade's delta taken at the day's moves (lin + 2·quad·y), so gamma raises it and cuts lower
it. A CCP also scales its margin by the realised volatility of the set's P&L (an EWMA seeded at σ_0,
floored at 1 and capped at ``ccp_margin_cap``, as anti-procyclicality tools damp it): margin is
procyclical. Margin received is segregated and never counts as liquidity.

**Liquidity** (bank) = the cash buffer set in Admin + cash in and out since today + HQLA: long
government bonds at their path value less a monetisation haircut, less the collateral pledged against
net repo borrowing. (Fund) = NAV − prime-broker margin, the margin following each position's gross
exposure on the path (HF-002); the 15% unencumbered cash is part of that excess equity.

**Funding scenarios** change the terms from day one: a downgrade sets our CSA thresholds to zero
(firm-specific), repo lenders ask a higher haircut when each repo rolls (firm-specific), collateral
haircuts multiply (market-wide).

**The spiral** (managed book): when a close leaves liquidity below zero the firm sells a share of every
position, bounded by the day's stressed volume net of what the desks already sold, at the MR-023 cost;
``permanent_impact_share`` of the square-root impact stays in the price and re-marks what is still
held, which calls more margin. The sale is resized and repeated to a fixed point, at most
``spiral_max_rounds`` rounds a close. A close that still ends below zero is the day the firm fails.
The frozen book never sells: it reports the day its liquidity runs out.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

RECORD = "MR-024"
MODEL_VERSION = "1.0.0"

FUNDING_SCENARIOS = ("NORMAL", "FIRM_SPECIFIC", "MARKET_WIDE")
RESOURCES = "(resources)"
LEVEL1_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD")
FUNDED_PRODUCTS = ("GOVERNMENT_BOND", "CASH_EQUITY", "ETF", "MUTUAL_FUND", "CRYPTO_SPOT")
LISTED_FUTURES = ("EQUITY_INDEX_FUTURE", "COMMODITY_FUTURE", "INTEREST_RATE_FUTURE")
LINEAR_EXPOSURE = ("CASH_EQUITY", "ETF", "CRYPTO_SPOT", "EQUITY_INDEX_FUTURE", "COMMODITY_FUTURE", "FX_SPOT")
"""Fund products whose gross exposure moves one for one with their P&L."""
CONTRACTUAL_KINDS = ("COUPON", "FIXED", "FLOAT", "PREMIUM", "PROTECTION", "SETTLEMENT")
"""Contractual flows counted inside the horizon; principals are not (repos roll, a bond's principal
turns an asset into cash)."""
HEDGE_MARGIN = {"RATES": 0.02, "EQUITY_INDEX": 0.06, "FX": 0.03, "COMMODITY": 0.10, "CRYPTO": 0.35}
"""Initial margin on the listed hedge futures, share of notional (assumption, MR-024)."""
REGIMES = ("VM", "CSA", "FUNDED", "UNMARGINED")
PROBE = 0.1
"""Share of every position the spiral sells to measure what a sale raises."""
OVERSHOOT = 1.05
"""A forced sale aims 5% above the shortfall, so costs and impact rarely need another round."""
TOLERANCE = 1e-6
"""A shortfall below this share of today's liquidity counts as met (the spiral's fixed point)."""


# --- the owner's rules ----------------------------------------------------------------------------
@dataclass(frozen=True)
class FundingScenario:
    """How funding terms change on the path, from day one."""

    scope: str = "NORMAL"
    threshold_we_post_multiplier: float = 1.0
    repo_haircut_addon: float = 0.0
    haircut_multiplier: float = 1.0
    ccp_procyclical: bool = True
    pb_margin_multiplier: float = 1.0

    def problems(self) -> list[str]:
        out = []
        if self.threshold_we_post_multiplier < 0:
            out.append(f"{self.scope}: the threshold multiplier cannot be negative")
        if not 0.0 <= self.repo_haircut_addon < 0.5:
            out.append(f"{self.scope}: the repo haircut add-on lies in [0, 0.5)")
        if self.haircut_multiplier < 1.0:
            out.append(f"{self.scope}: the haircut multiplier is at least 1")
        if self.pb_margin_multiplier < 1.0:
            out.append(f"{self.scope}: the prime-broker margin multiplier is at least 1")
        return out


@dataclass(frozen=True)
class FundingResources:
    """What the firm holds against calls, and the spiral's parameters."""

    scope: str = RESOURCES
    cash_buffer: float = 1.0e9
    hqla_haircut_level1: float = 0.02
    hqla_haircut_other: float = 0.15
    im_collateral_haircut: float = 0.02
    permanent_impact_share: float = 0.5
    spiral_max_rounds: int = 10
    ewma_lambda: float = 0.94
    ccp_margin_cap: float = 2.0

    def problems(self) -> list[str]:
        out = []
        if self.cash_buffer < 0:
            out.append("the cash buffer cannot be negative")
        for name in ("hqla_haircut_level1", "hqla_haircut_other", "im_collateral_haircut"):
            if not 0.0 <= getattr(self, name) < 0.5:
                out.append(f"{name} lies in [0, 0.5)")
        if not 0.0 <= self.permanent_impact_share <= 1.0:
            out.append("the permanent impact share lies in [0, 1]")
        if not 1 <= int(self.spiral_max_rounds) <= 50:
            out.append("the spiral runs 1 to 50 rounds a close")
        if not 0.0 < self.ewma_lambda < 1.0:
            out.append("the EWMA decay lies in (0, 1)")
        if self.ccp_margin_cap < 1.0:
            out.append("the CCP margin cap is at least 1")
        return out


DEFAULT_SCENARIOS = (
    FundingScenario("NORMAL"),
    FundingScenario("FIRM_SPECIFIC", threshold_we_post_multiplier=0.0, repo_haircut_addon=0.05),
    FundingScenario("MARKET_WIDE", haircut_multiplier=2.0),
)
DEFAULT_RESOURCES = FundingResources()


@dataclass(frozen=True)
class FundingRules:
    resources: FundingResources = DEFAULT_RESOURCES
    scenarios: tuple[FundingScenario, ...] = DEFAULT_SCENARIOS

    def to_list(self) -> list[dict[str, Any]]:
        return [asdict(self.resources)] + [asdict(s) for s in self.scenarios]

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> FundingRules:
        res = DEFAULT_RESOURCES
        scen: list[FundingScenario] = []
        for r in rows:
            d = dict(r)
            target = FundingResources if d.get("scope") == RESOURCES else FundingScenario
            names = {f.name for f in fields(target)}
            unknown = sorted(set(d) - names)
            if unknown:
                raise ValueError(f"unknown funding fields for {d.get('scope')}: {', '.join(unknown)}")
            if target is FundingResources:
                res = FundingResources(**d)
            else:
                scen.append(FundingScenario(**d))
        order = {s: i for i, s in enumerate(FUNDING_SCENARIOS)}
        return cls(res, tuple(sorted(scen, key=lambda s: order.get(s.scope, 99))))

    def problems(self) -> list[str]:
        out = self.resources.problems()
        scopes = [s.scope for s in self.scenarios]
        if sorted(scopes) != sorted(FUNDING_SCENARIOS):
            out.append(f"the funding scenarios are exactly {', '.join(FUNDING_SCENARIOS)}")
        for s in self.scenarios:
            out += s.problems()
        return out


# --- what the chain needs from the firm ----------------------------------------------------------
@dataclass
class FundingInputs:
    """The firm's margin terms, built once per run (``workflows.stress_paths.funding_inputs``)."""

    face: str  # bank or fund
    as_of: date
    trades: list[str]
    pv: np.ndarray  # reporting currency, today
    regime: np.ndarray  # one of REGIMES per trade
    netting_set: np.ndarray  # CSA or CCP set id per trade, "" otherwise
    hqla_haircut: np.ndarray  # base monetisation haircut per trade; nan if not HQLA
    csa: dict[str, dict[str, float]] = field(default_factory=dict)
    """CSA set -> threshold_we_post, threshold_they_post, haircut."""
    im_posted_model: dict[str, float] = field(default_factory=dict)
    """Netting set -> SIMM-lite margin we would post today (before the threshold)."""
    im_counterparty: dict[str, str] = field(default_factory=dict)
    ccp_sets: frozenset[str] = frozenset()
    im_threshold: float = 0.0
    repos: list[dict[str, Any]] = field(default_factory=list)
    """cash (signed: + borrowed), haircut, end_date for every live repo."""
    flows: np.ndarray | None = None  # trades x horizon: contractual cash per day
    # Fund.
    nav: float = 0.0
    margin_total: float = 0.0
    margin_rate: np.ndarray | None = None
    exposure: np.ndarray | None = None
    linear: np.ndarray | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "face": self.face,
            "regimes": {r: int((self.regime == r).sum()) for r in REGIMES},
            "csa_sets": len(self.csa),
            "im_sets": len(self.im_posted_model),
            "repos": len(self.repos),
        }


def business_dates(as_of: date, horizon: int) -> list[date]:
    days = np.busday_offset(np.datetime64(as_of, "D"), np.arange(1, horizon + 1), roll="forward")
    return [pd.Timestamp(d).date() for d in days]


def regime_of(
    product: str, direction: str, clearing: str, counterparty: str, has_csa: bool, ccp: bool
) -> str:
    if ccp:
        return "VM"
    if product in FUNDED_PRODUCTS:
        return "FUNDED" if direction == "BUY" else "VM"
    if clearing == "EXCHANGE" or counterparty.startswith("EXCH_"):
        if product in LISTED_FUTURES or direction == "SELL":
            return "VM"
        return "UNMARGINED"
    if has_csa and product != "REPO":
        return "CSA"
    return "UNMARGINED"


# --- one walk -------------------------------------------------------------------------------------
@dataclass
class _Day:
    variation_margin: float = 0.0
    collateral: float = 0.0
    initial_margin: float = 0.0
    hqla: float = 0.0
    contractual: float = 0.0
    sale_proceeds: float = 0.0
    costs: float = 0.0
    net_liquidity: float = 0.0
    forced_sold: float = 0.0
    spiral_rounds: int = 0
    permanent_impact: float = 0.0


class FundingWalk:
    """The cash side of one path under one funding scenario. The path engine calls ``close`` at
    every close of the managed book (and ``frozen`` once for the frozen book)."""

    def __init__(
        self,
        inputs: FundingInputs,
        rules: FundingRules,
        scenario: FundingScenario,
        loadings: list[np.ndarray],
        set_matrix: sparse.csr_matrix,
        set_ids: list[str],
        sigma0: np.ndarray,
        hedge_kinds: list[str],
        hedge_conv: np.ndarray,
    ) -> None:
        self.x, self.rules, self.s = inputs, rules, scenario
        self.r = rules.resources
        self.loadings = loadings  # per close t = 0..H: trades x k
        self.S, self.set_ids, self.sigma0 = set_matrix, set_ids, sigma0
        n = len(inputs.trades)
        self.n = n
        reg = inputs.regime
        self.vm = reg == "VM"
        self.funded = reg == "FUNDED"
        self.pv_settling = (reg == "FUNDED") | (reg == "UNMARGINED") | (reg == "CSA")
        self.hqla = ~np.isnan(inputs.hqla_haircut)
        self.hqla_hc = np.nan_to_num(inputs.hqla_haircut)
        self.hedge_margin = np.array([HEDGE_MARGIN.get(k, 0.05) for k in hedge_kinds])
        self.hedge_conv = hedge_conv
        # CSA sets, as positions in set_ids.
        self.csa_pos = [i for i, k in enumerate(set_ids) if k in inputs.csa]
        self.thr_we = np.array([inputs.csa[set_ids[i]]["threshold_we_post"] for i in self.csa_pos])
        self.thr_they = np.array([inputs.csa[set_ids[i]]["threshold_they_post"] for i in self.csa_pos])
        self.csa_hc = np.array([inputs.csa[set_ids[i]]["haircut"] for i in self.csa_pos])
        self.im_pos = [i for i, k in enumerate(set_ids) if k in inputs.im_posted_model]
        self.im_model0 = np.array([inputs.im_posted_model[set_ids[i]] for i in self.im_pos])
        self.im_ccp = np.array([set_ids[i] in inputs.ccp_sets for i in self.im_pos], dtype=bool)
        cps = [inputs.im_counterparty.get(set_ids[i], set_ids[i]) for i in self.im_pos]
        self.im_cp_ids = sorted(set(cps))
        cpos = {c: j for j, c in enumerate(self.im_cp_ids)}
        self.im_cp = np.array([cpos[c] for c in cps], dtype=int)
        self.dates = business_dates(inputs.as_of, len(loadings) - 1)
        # Base state: today's terms and values.
        self.v0 = np.asarray(self.S @ inputs.pv).ravel()
        self.ewma0 = sigma0**2
        self._base = self._terms(np.ones(n), np.zeros(n), 0, np.ones(len(self.im_pos)), base=True)
        self._start = self.start()

    # -- pieces of liquidity ----------------------------------------------------------------------
    def _im_posted(self, model: np.ndarray) -> np.ndarray:
        """Margin posted per set: bilateral sets above the threshold per counterparty, pro rata; CCP
        sets in full (as REG-004)."""
        bil = np.where(self.im_ccp, 0.0, model)
        tot = np.bincount(self.im_cp, weights=bil, minlength=len(self.im_cp_ids))
        above = np.maximum(tot - self.x.im_threshold, 0.0)
        share = np.divide(above, tot, out=np.zeros_like(tot), where=tot > 0)
        return np.where(self.im_ccp, model, bil * share[self.im_cp])

    def _terms(
        self, h: np.ndarray, cum: np.ndarray, t: int, pro: np.ndarray, base: bool = False
    ) -> dict[str, float]:
        """Collateral, margin and HQLA at close ``t`` for held shares ``h`` and cumulative trade P&L
        ``cum``; ``base`` uses today's terms (no scenario)."""
        x, r = self.x, self.r
        s = FundingScenario("BASE") if base else self.s
        value = h * (x.pv + cum)
        out: dict[str, float] = {}
        if x.face == "fund":
            e = h * (x.exposure + x.linear * cum)  # type: ignore[operator]
            base_gross = float(np.abs(x.exposure) @ x.margin_rate)  # type: ignore[arg-type]
            gross = float(np.abs(e) @ x.margin_rate)  # type: ignore[arg-type]
            scale = gross / base_gross if base_gross > 0 else 1.0
            out["margin"] = x.margin_total * scale * s.pb_margin_multiplier
            return out
        # CSA collateral: net held (+) or posted (-) cash equivalent, and the haircut on what we post.
        v = np.asarray(self.S @ value).ravel()
        if self.csa_pos:
            vc = v[self.csa_pos]
            we = np.maximum(-vc - self.thr_we * s.threshold_we_post_multiplier, 0.0)
            they = np.maximum(vc - self.thr_they, 0.0)
            hc = np.minimum(self.csa_hc * s.haircut_multiplier, 0.95)
            out["collateral"] = float((they - we).sum())
            out["collateral_haircut"] = float((we * (1.0 / (1.0 - hc) - 1.0)).sum())
        else:
            out["collateral"] = out["collateral_haircut"] = 0.0
        # Initial margin we post, at the stressed delta.
        if self.im_pos:
            ld = self.loadings[t]
            sig = np.sqrt((np.asarray(self.S[self.im_pos] @ (h[:, None] * ld)) ** 2).sum(axis=1))
            ratio = np.divide(
                sig, self.sigma0[self.im_pos], out=np.ones_like(sig), where=self.sigma0[self.im_pos] > 0
            )
            if base:
                ratio = np.ones_like(sig)
            posted = self._im_posted(self.im_model0 * ratio * np.where(self.im_ccp, pro, 1.0))
            ihc = min(r.im_collateral_haircut * s.haircut_multiplier, 0.95)
            out["im_posted"] = float(posted.sum())
            out["im_haircut"] = float(posted.sum() * (1.0 / (1.0 - ihc) - 1.0))
        else:
            out["im_posted"] = out["im_haircut"] = 0.0
        # HQLA less net repo encumbrance; a repo rolling inside the horizon takes the add-on.
        hc = np.minimum(self.hqla_hc * s.haircut_multiplier, 0.95)
        out["hqla_assets"] = float((value * (1.0 - hc))[self.hqla].sum())
        day = self.dates[t - 1] if t >= 1 else x.as_of
        enc = 0.0
        for rp in x.repos:
            add = s.repo_haircut_addon if (rp["cash"] > 0 and t >= 1 and rp["end_date"] <= day) else 0.0
            enc += rp["cash"] * (1.0 + rp["haircut"] + add)
        out["encumbrance"] = enc
        return out

    def liquidity(self, terms: dict[str, float], cash: float, pnl: float, hedge: np.ndarray) -> float:
        """Net liquidity from the terms, the cash paid and received since today and (fund) the P&L."""
        hm = (
            float(
                (np.abs(hedge).sum(axis=0) / np.where(self.hedge_conv > 0, self.hedge_conv, 1.0))
                @ self.hedge_margin
            )
            if hedge.size
            else 0.0
        )
        if self.x.face == "fund":
            return self.x.nav + pnl - terms["margin"] - hm
        b = self._base
        return (
            self.r.cash_buffer
            + cash
            + (terms["collateral"] - b["collateral"])
            - (terms["collateral_haircut"] - b["collateral_haircut"])
            - (terms["im_posted"] - b["im_posted"])
            - (terms["im_haircut"] - b["im_haircut"])
            + terms["hqla_assets"]
            - terms["encumbrance"]
            - hm
        )

    def start(self) -> float:
        return self.liquidity(self._base, 0.0, 0.0, np.zeros(0))

    # -- the walk ----------------------------------------------------------------------------------
    def begin(self) -> None:
        self.cash = 0.0
        self.pnl = 0.0
        self.ewma = self.ewma0.copy()
        self.days: list[_Day] = []
        self.remark = np.zeros(self.n)
        self.failed_day: int | None = None
        self.unresolved = False

    def _pro(self, day_set_pnl: np.ndarray) -> np.ndarray:
        lam = self.r.ewma_lambda
        self.ewma = lam * self.ewma + (1.0 - lam) * day_set_pnl**2
        ratio = np.divide(
            np.sqrt(self.ewma), self.sigma0, out=np.ones_like(self.sigma0), where=self.sigma0 > 0
        )
        pro = np.clip(ratio, 1.0, self.r.ccp_margin_cap) if self.s.ccp_procyclical else np.ones_like(ratio)
        return pro[self.im_pos] if self.im_pos else np.zeros(0)

    def close(
        self,
        t: int,
        h_prev: np.ndarray,
        h: np.ndarray,
        trade_pnl: np.ndarray,
        cum: np.ndarray,
        costs: float,
        hedge_pnl: float,
        hedge: np.ndarray,
        sell: Any | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Close ``t`` (1-based). ``trade_pnl``: today's P&L per trade on the held shares;
        ``cum``: cumulative P&L per trade at full size (path move plus theta), for valuation;
        ``sell`` (``room(h)``, ``trade(dh) -> (extra cost per trade, extra impact per unit position)``)
        prices a forced sale. Returns the
        forced reduction, its cost and the permanent re-mark per trade (all zero when no sale)."""
        d = _Day()
        cum = cum + self.remark
        day_cash = float(trade_pnl[self.vm].sum()) + hedge_pnl - costs
        # Fund: every position sits in a prime-broker account marked daily, so all P&L is cash there.
        d.variation_margin = (
            float(trade_pnl.sum()) + hedge_pnl
            if self.x.face == "fund"
            else float(trade_pnl[self.vm].sum()) + hedge_pnl
        )
        sold = np.clip(h_prev - h, 0.0, None)
        proceeds = float((sold * (self.x.pv + cum))[self.pv_settling].sum())
        if self.x.face == "fund":
            proceeds = 0.0
        flows = 0.0
        if self.x.flows is not None and t - 1 < self.x.flows.shape[1]:
            flows = float(h_prev @ self.x.flows[:, t - 1])
        self.cash += day_cash + proceeds + flows
        self.pnl += float(trade_pnl.sum()) + hedge_pnl - costs
        set_pnl = np.asarray(self.S @ (h_prev * trade_pnl)).ravel()
        pro = self._pro(set_pnl)
        terms = self._terms(h, cum, t, pro)
        liq = self.liquidity(terms, self.cash, self.pnl, hedge)
        d.sale_proceeds, d.contractual, d.costs = proceeds, flows, costs
        n = self.n
        dh_all, cost_all, remark_all = np.zeros(n), np.zeros(n), np.zeros(n)
        rounds = 0
        tol = -TOLERANCE * max(abs(self._start), 1.0)
        if liq < tol and sell is not None and self.failed_day is None:
            room = np.minimum(sell.room(h), h)
            while liq < tol and rounds < int(self.r.spiral_max_rounds):
                # Size the sale on a probe of 10% of every position, before costs and impact; the
                # next round corrects for them (the fixed point).
                probe = np.minimum(PROBE * h, room)
                if probe.sum() <= 0:
                    break
                gain = self._gain(h, probe, cum, t, pro, liq, hedge)
                if gain <= 0:
                    break
                dh = np.minimum(min(-liq / gain * OVERSHOOT, 1.0 / PROBE) * probe, room)
                if dh.sum() <= 0:
                    break
                rounds += 1
                cost_tr, remark = sell.trade(dh)
                remark = remark * self.r.permanent_impact_share
                proceeds = (
                    float((dh * (self.x.pv + cum))[self.pv_settling].sum()) if self.x.face != "fund" else 0.0
                )
                vm_remark = float(((h - dh) * remark)[self.vm].sum())
                self.cash += proceeds - float(cost_tr.sum()) - vm_remark
                self.pnl -= float(cost_tr.sum()) + float(((h - dh) * remark).sum())
                h = h - dh
                room = np.maximum(room - dh, 0.0)
                self.remark -= remark
                cum = cum - remark
                dh_all += dh
                cost_all += cost_tr
                remark_all += h * remark
                d.sale_proceeds += proceeds
                d.costs += float(cost_tr.sum())
                d.permanent_impact += float((h * remark).sum())
                terms = self._terms(h, cum, t, pro)
                liq = self.liquidity(terms, self.cash, self.pnl, hedge)
        if liq < tol and self.failed_day is None:
            self.failed_day = t
            self.unresolved = rounds > 0
        b = self._base
        if self.x.face == "fund":
            d.initial_margin = -(terms["margin"] - self.x.margin_total)
        else:
            d.collateral = (terms["collateral"] - b["collateral"]) - (
                terms["collateral_haircut"] - b["collateral_haircut"]
            )
            d.initial_margin = -(terms["im_posted"] - b["im_posted"]) - (
                terms["im_haircut"] - b["im_haircut"]
            )
            d.hqla = (terms["hqla_assets"] - b["hqla_assets"]) - (terms["encumbrance"] - b["encumbrance"])
        d.net_liquidity = liq
        d.forced_sold = float(dh_all.sum())
        d.spiral_rounds = rounds
        self.days.append(d)
        return dh_all, cost_all, remark_all

    def _gain(
        self,
        h: np.ndarray,
        probe: np.ndarray,
        cum: np.ndarray,
        t: int,
        pro: np.ndarray,
        liq: float,
        hedge: np.ndarray,
    ) -> float:
        """Liquidity raised by selling ``probe`` before costs and impact."""
        terms = self._terms(h - probe, cum, t, pro)
        proceeds = (
            float((probe * (self.x.pv + cum))[self.pv_settling].sum()) if self.x.face != "fund" else 0.0
        )
        return self.liquidity(terms, self.cash + proceeds, self.pnl, hedge) - liq


# --- reporting ------------------------------------------------------------------------------------
SUMMARY_COLUMNS = [
    "path_id",
    "book",
    "funding_scenario",
    "start_liquidity",
    "min_liquidity",
    "min_liquidity_day",
    "end_liquidity",
    "peak_cash_out",
    "survival_day",
    "survives",
    "variation_margin",
    "collateral",
    "initial_margin",
    "hqla",
    "contractual",
    "forced_sale_days",
    "forced_sold",
    "forced_sale_cost",
    "permanent_impact",
    "spiral_rounds_max",
    "unresolved",
    "end_pnl",
]
DAY_COLUMNS = [
    "path_id",
    "book",
    "funding_scenario",
    "day",
    "variation_margin",
    "collateral",
    "initial_margin",
    "hqla",
    "contractual",
    "sale_proceeds",
    "costs",
    "net_liquidity",
    "forced_sold",
    "spiral_rounds",
    "permanent_impact",
]
INSTANT_COLUMNS = [
    "scenario_id",
    "name",
    "stress_set",
    "funding_scenario",
    "pnl",
    "variation_margin",
    "collateral",
    "initial_margin",
    "hqla",
    "net_call",
    "start_liquidity",
    "net_liquidity",
    "survives",
]


def summarise_walk(
    walk: FundingWalk, path_id: str, book: str, end_pnl: float, forced_cost: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start = walk.start()
    liq = np.array([d.net_liquidity for d in walk.days])
    days = []
    cum = {k: 0.0 for k in ("variation_margin", "contractual")}
    for i, d in enumerate(walk.days, 1):
        cum["variation_margin"] += d.variation_margin
        cum["contractual"] += d.contractual
        days.append(
            {
                "path_id": path_id,
                "book": book,
                "funding_scenario": walk.s.scope,
                "day": i,
                **{k: getattr(d, k) for k in DAY_COLUMNS[4:]},
            }
        )
    last = walk.days[-1]
    imin = int(np.argmin(liq))
    forced = [d for d in walk.days if d.forced_sold > 0]
    row = {
        "path_id": path_id,
        "book": book,
        "funding_scenario": walk.s.scope,
        "start_liquidity": start,
        "min_liquidity": float(liq[imin]),
        "min_liquidity_day": imin + 1,
        "end_liquidity": float(liq[-1]),
        "peak_cash_out": float(max(start - liq.min(), 0.0)),
        "survival_day": walk.failed_day,
        "survives": walk.failed_day is None,
        "variation_margin": cum["variation_margin"],
        "collateral": last.collateral,
        "initial_margin": last.initial_margin,
        "hqla": last.hqla,
        "contractual": cum["contractual"],
        "forced_sale_days": len(forced),
        "forced_sold": float(sum(d.forced_sold for d in walk.days)),
        "forced_sale_cost": forced_cost,
        "permanent_impact": float(sum(d.permanent_impact for d in walk.days)),
        "spiral_rounds_max": int(max((d.spiral_rounds for d in walk.days), default=0)),
        "unresolved": bool(walk.unresolved),
        "end_pnl": end_pnl,
    }
    return row, days


# --- the chain over every path ---------------------------------------------------------------------
@dataclass
class LiquidityResult:
    summary_table: pd.DataFrame
    """One row per path, book (FROZEN, MANAGED) and funding scenario."""
    days: pd.DataFrame
    instant: pd.DataFrame
    """Every stress scenario as an instantaneous call, per funding scenario (before any action)."""
    parameters: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        s = self.summary_table
        if s.empty:
            return {}
        mg = s[s["book"] == "MANAGED"]
        fr = s[s["book"] == "FROZEN"]
        worst = mg.sort_values("min_liquidity", kind="stable").iloc[0]
        fails = mg[~mg["survives"]]
        inst = self.instant
        wi = inst.sort_values("net_liquidity", kind="stable").iloc[0] if len(inst) else None
        return {
            "liquidity_start": float(s["start_liquidity"].iloc[0]),
            "liquidity_worst_path_id": str(worst["path_id"]),
            "liquidity_worst_funding": str(worst["funding_scenario"]),
            "liquidity_worst_min": float(worst["min_liquidity"]),
            "liquidity_failures_frozen": int((~fr["survives"]).sum()),
            "liquidity_failures_managed": int(len(fails)),
            "liquidity_first_failure_day": int(fails["survival_day"].min()) if len(fails) else None,
            "liquidity_spiral_paths": int((mg["forced_sale_days"] > 0).sum()),
            "liquidity_worst_instant_scenario": str(wi["scenario_id"]) if wi is not None else None,
            "liquidity_worst_instant": float(wi["net_liquidity"]) if wi is not None else None,
        }


class FundingChain:
    """Builds the netting-set structure once, then walks each path under every funding scenario."""

    def __init__(self, engine: Any, inputs: FundingInputs, rules: FundingRules | None) -> None:
        self.engine, self.x = engine, inputs
        self.rules = rules or FundingRules()
        if list(inputs.trades) != list(engine.book.trades):
            raise ValueError("funding inputs and the path engine disagree on the trades")
        keys = set(inputs.csa) | set(inputs.im_posted_model) | set(inputs.ccp_sets)
        self.set_ids = sorted(k for k in keys if k)
        pos = {k: i for i, k in enumerate(self.set_ids)}
        rows = [(pos[k], j) for j, k in enumerate(inputs.netting_set) if k in pos]
        self.S = sparse.csr_matrix(
            (np.ones(len(rows)), ([r for r, _ in rows], [c for _, c in rows])),
            shape=(len(self.set_ids), len(inputs.trades)),
        )
        self.sigma0 = (
            np.sqrt((np.asarray(self.S @ engine.loading) ** 2).sum(axis=1))
            if rows
            else np.zeros(len(self.set_ids))
        )
        self.summary: list[dict[str, Any]] = []
        self.days: list[dict[str, Any]] = []
        self.instant_rows: list[dict[str, Any]] = []

    def _walk(
        self, scenario: FundingScenario, loadings: list[np.ndarray], inputs: FundingInputs | None = None
    ) -> FundingWalk:
        hg = self.engine.hedges
        return FundingWalk(
            inputs or self.x,
            self.rules,
            scenario,
            loadings,
            self.S,
            self.set_ids,
            self.sigma0,
            hg.kinds,
            hg.conv,
        )

    def walk_path(self, path: Any, full: np.ndarray, frozen_end: float) -> None:
        loadings = self.engine.stressed_loadings(path)
        for sc in self.rules.scenarios:
            w = self._walk(sc, loadings)
            self.engine.frozen_walk(w, path, full)
            row, days = summarise_walk(w, path.path_id, "FROZEN", frozen_end, 0.0)
            self.summary.append(row)
            self.days += days
            w = self._walk(sc, loadings)
            o = self.engine.run(path, full, w)
            row, days = summarise_walk(
                w, path.path_id, "MANAGED", float(o.summary[1]["end_pnl"]), o.forced_cost
            )
            self.summary.append(row)
            self.days += days

    def instant(self, results: list[Any], relative: np.ndarray, limit_ids: set[str]) -> None:
        """Each stress scenario as one instantaneous move: the call it makes on liquidity before any
        action (framework part D). No contractual flows and no sale."""
        from novera.risk.reverse_stress import PRICE_FLOOR

        factors = self.engine.book.factors
        fpos = {f: i for i, f in enumerate(factors)}
        tpos = {t: i for i, t in enumerate(self.x.trades)}
        quiet = FundingInputs(**{**self.x.__dict__, "flows": None})
        for r in results:
            end = np.zeros(len(factors))
            for f, v in r.shocks.items():
                if f in fpos:
                    end[fpos[f]] = v
            end[relative] = np.maximum(end[relative], PRICE_FLOOR)
            moves = np.vstack([np.zeros(len(factors)), end])
            loadings = self.engine.stressed_loadings(_Moves(moves))
            pnl = np.zeros(len(self.x.trades))
            for tid, v in r.pnl.items():
                if tid in tpos:
                    pnl[tpos[tid]] = float(v)
            h = np.ones(len(pnl))
            for sc in self.rules.scenarios:
                w = self._walk(sc, loadings, quiet)
                w.begin()
                w.close(1, h, h, pnl, pnl, 0.0, 0.0, np.zeros((0, 0)))
                d = w.days[0]
                start = w.start()
                self.instant_rows.append(
                    {
                        "scenario_id": r.scenario.scenario_id,
                        "name": r.scenario.name,
                        "stress_set": "LIMIT" if r.scenario.scenario_id in limit_ids else "INFORMATION",
                        "funding_scenario": sc.scope,
                        "pnl": float(pnl.sum()),
                        "variation_margin": d.variation_margin,
                        "collateral": d.collateral,
                        "initial_margin": d.initial_margin,
                        "hqla": d.hqla,
                        "net_call": start - d.net_liquidity,
                        "start_liquidity": start,
                        "net_liquidity": d.net_liquidity,
                        "survives": d.net_liquidity >= 0,
                    }
                )

    def result(self) -> LiquidityResult:
        return LiquidityResult(
            pd.DataFrame(self.summary, columns=SUMMARY_COLUMNS),
            pd.DataFrame(self.days, columns=DAY_COLUMNS),
            pd.DataFrame(self.instant_rows, columns=INSTANT_COLUMNS),
            {
                "record": RECORD,
                "face": self.x.face,
                "netting_sets": len(self.set_ids),
                **{f"regime_{k}": v for k, v in self.x.summary()["regimes"].items()},
                "repos": len(self.x.repos),
                "rules": self.rules.to_list(),
            },
        )


@dataclass
class _Moves:
    moves: np.ndarray
