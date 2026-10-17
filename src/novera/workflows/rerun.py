"""Partial re-run of one EOD stage on a stored run (OPS-002).

A re-run never edits the parent run. It creates a new run of type RERUN with the same
business date, snapshots, configuration and model versions, copies every stored result table
of the parent, recomputes the requested stage from the same inputs and replaces only that
stage's tables. Upstream results a stage needs are recomputed in memory: the engine is
deterministic, so they reproduce the stored numbers. Downstream stages are copied unchanged
and listed as not recomputed. Re-runs never sync breaches, dispatch alerts, or count as the
latest EOD run or in the live backtest.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from novera.config import get_settings
from novera.counterparty_risk import ExposureSimConfig, run_counterparty
from novera.fund import run_fund
from novera.limits import RiskInputs, effective_limits, monitor
from novera.market_data.history import MarketHistory
from novera.pricing.collateral import CollateralMap
from novera.pricing.valuation import value_portfolio
from novera.regulatory import run_regulatory
from novera.risk import (
    Portfolio,
    VaRConfig,
    compute_sensitivities,
    concentration,
    explain_pnl,
    history_backtest,
    liquidity,
    live_backtest,
    look_through,
    static_backtest,
)
from novera.risk.factor_stress import run_factor_stress
from novera.risk.stress_definitions import (
    built_in_definitions,
    episode_windows,
    run_stress_definitions,
    stress_headline,
)
from novera.risk.stress_explain import run_stress_explain
from novera.risk.var_measures import DEFAULT_MEASURES, MeasureSet, VaRMeasure, compute_measures
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import (
    MODEL_VERSIONS,
    EODConfig,
    persist_backtest,
    persist_concentration,
    persist_factor_stress,
    persist_pnl,
    persist_stress,
    persist_stress_explain,
    persist_var,
    run_dir,
)
from novera.workflows.liquidation_stress import LIQUIDATION_TABLES
from novera.workflows.pca_stress import (
    REVERSE_STRESS_TABLES,
    STRESS_MC_TABLES,
    persist_reverse_stress,
    persist_stress_mc,
    run_pca_stress,
)
from novera.workflows.runs import USABLE_STATUSES, AuditEvent, RunRecord, new_run_id
from novera.workflows.stress_paths import STRESS_PATH_TABLES, persist_stress_paths
from novera.workflows.stress_paths import run_stage as run_stress_paths_stage
from novera.workflows.sweep import owner, sweep_stuck

MODEL_VERSION = "1.1.0"
RECORD = "OPS-002"


@dataclass(frozen=True)
class Stage:
    name: str
    title: str
    description: str
    tables: tuple[str, ...]
    dependents: tuple[str, ...]
    """Stages whose stored results are copied from the parent and would change if this stage's
    numbers changed; the re-run lists them as not recomputed."""
    summary_keys: tuple[str, ...]
    faces: tuple[str, ...] = ("bank", "fund")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tables": list(self.tables),
            "dependents": list(self.dependents),
            "summary_keys": list(self.summary_keys),
            "faces": list(self.faces),
        }


STAGES: tuple[Stage, ...] = (
    Stage(
        "valuation",
        "Valuation",
        "Re-price every trade on the run's market snapshot, with the proxies the run applied.",
        ("valuation",),
        (
            "sensitivities",
            "var",
            "backtest",
            "stress",
            "limits",
            "concentration",
            "pnl",
            "regulatory",
            "counterparty",
            "fund",
        ),
        ("pv", "priced_trades", "unpriced_trades"),
    ),
    Stage(
        "sensitivities",
        "Sensitivities",
        "Bump-and-revalue ladders for every measure (MR-001).",
        ("sensitivities",),
        ("var", "limits", "concentration", "regulatory", "fund"),
        (),
    ),
    Stage(
        "var",
        "VaR and ES",
        "Every measure of the run's VaR setup (OPS-004): historical, weighted and stressed VaR, "
        "ES, the delta-gamma-vega challenger and Monte Carlo, including the scenario matrices "
        "(MR-002 to MR-004, MR-010, MR-015, MR-016).",
        (
            "var_summary",
            "var_contributions",
            "var_contributions_challenger",
            "var_contributions_monte_carlo",
            "var_measure_contributions",
            "var_measure_scenarios",
            "var_scenarios",
        ),
        ("backtest", "limits", "concentration", "regulatory", "fund"),
        (
            "var",
            "es",
            "es_confidence",
            "var_scaled",
            "var_scenario_date",
            "var_confidence",
            "var_measure_id",
            "stressed_var",
            "challenger_var",
            "monte_carlo_var",
            "monte_carlo_es",
            "var_measures",
        ),
    ),
    Stage(
        "backtest",
        "Backtest",
        "Static backtest on the scenario vector, the live backtest across stored EOD runs and the "
        "history backtest for information (MR-011).",
        ("backtest_summary", "backtest_series", "backtest_live_series", "backtest_history_series"),
        (),
        ("backtest_zone", "backtest_exceptions", "backtest_days"),
    ),
    Stage(
        "stress",
        "Stress",
        "The scenarios the run ran (OPS-005) under full revaluation (MR-005).",
        ("stress", "stress_summary"),
        ("limits", "counterparty"),
        (
            "worst_stress_id",
            "worst_stress_name",
            "worst_stress",
            "worst_stress_all_id",
            "worst_stress_all_name",
            "worst_stress_all",
            "stress_scenarios",
            "stress_limit_scenarios",
        ),
    ),
    Stage(
        "factor_stress",
        "Factor stress",
        "Standalone shocks of every factor, sliding scales, jump to default by issuer and the worst "
        "historical windows, for information (MR-019).",
        ("factor_stress", "factor_stress_scales", "jump_to_default", "worst_windows"),
        (),
        (
            "factor_stress_factors",
            "worst_factor_id",
            "worst_factor_direction",
            "worst_factor",
            "worst_jtd_issuer",
            "worst_jtd",
            "worst_window_start",
            "worst_window_end",
            "worst_window_days",
            "worst_window_version",
            "worst_window",
        ),
    ),
    Stage(
        "stress_explain",
        "Stress explain",
        "Each stress scenario's loss by factor family (delta, gamma, vega, higher order, cross-family), "
        "its worst trades, the hedge-failure line and the summary of significant risks (MR-020).",
        ("stress_explain", "stress_top_trades", "risk_summary"),
        (),
        ("hedge_failure", "hedge_failure_scenario", "significant_risks"),
    ),
    Stage(
        "reverse_stress",
        "Reverse stress",
        "For each stress-loss limit, the most plausible scenarios in PCA space that lose exactly the limit, "
        "with near neighbours, confirmed in full revaluation (MR-021).",
        REVERSE_STRESS_TABLES,
        (),
        (
            "pca_components",
            "pca_explained",
            "pca_tail_capture",
            "reverse_stress_targets",
            "reverse_stress_unreachable",
            "reverse_firm_target",
            "reverse_firm_sigmas",
            "reverse_firm_probability",
            "reverse_easiest_target",
            "reverse_easiest_sigmas",
        ),
    ),
    Stage(
        "stress_monte_carlo",
        "Stressed Monte Carlo",
        "Ten-day stressed paths on fat-tailed PCA scores, Gaussian beside fat-tailed, and the share of "
        "paths that lose each stress-loss limit (MR-022).",
        STRESS_MC_TABLES,
        (),
        ("stress_mc_var_99", "stress_mc_es_975", "stress_mc_var_999", "stress_mc_tail_ratio"),
    ),
    Stage(
        "limits",
        "Limits",
        "Limit monitoring against the run's measures (MR-006), using the counterparty and fund "
        "measures stored on the parent. Never raises, escalates or closes a breach.",
        ("limits",),
        (),
        ("limits_monitored", "breaches", "warnings"),
    ),
    Stage(
        "concentration",
        "Concentration and liquidity",
        "Concentration, liquidity and fund look-through (MR-012 to MR-014).",
        (
            "concentration",
            "concentration_top",
            "concentration_tenor",
            "liquidity_trades",
            "liquidity_buckets",
            "liquidity_desks",
            "lookthrough_holdings",
            "lookthrough_constituents",
            "risk_flags",
        ),
        (),
        (
            "liquidity_adjusted_var",
            "liquidity_horizon_days",
            "concentration_flags",
            "liquidity_flags",
            "lookthrough_flags",
            "fund_holdings",
        ),
    ),
    Stage(
        "stress_paths",
        "Stress paths",
        "Twenty-day paths of the LIMIT scenarios in three shapes, the worst historical window and the "
        "fat-tailed Monte Carlo tail, revalued in full every day, walked frozen and managed by the "
        "action rules the parent run recorded (MR-023), with the margin, collateral and funding chain and "
        "the liquidity spiral under the recorded funding rules (MR-024).",
        STRESS_PATH_TABLES,
        (),
        (
            "stress_paths",
            "path_worst_id",
            "path_worst_drawdown_frozen",
            "path_worst_drawdown_managed",
            "path_worst_action_cost",
            "path_first_breach_day",
            "path_unfinished",
            "liquidity_start",
            "liquidity_worst_path_id",
            "liquidity_worst_funding",
            "liquidity_worst_min",
            "liquidity_failures_frozen",
            "liquidity_failures_managed",
            "liquidity_first_failure_day",
            "liquidity_spiral_paths",
            "liquidity_worst_instant_scenario",
            "liquidity_worst_instant",
        ),
    ),
    Stage(
        "liquidation_stress",
        "Liquidation stress",
        "Stressed exit horizons per market, the cost of getting out (spread and square-root impact) and its "
        "concentration add-on, every scenario's loss scaled for slow exits, and sector shocks (MR-025).",
        LIQUIDATION_TABLES,
        (),
        (
            "liquidation_exit_cost",
            "liquidation_concentration_addon",
            "liquidation_longest_market",
            "liquidation_longest_days",
            "liquidation_markets_over_10d",
            "liquidation_worst_scenario",
            "liquidation_worst_loss",
            "liquidation_worst_plain",
            "liquidation_largest_horizon_addon_scenario",
            "liquidation_largest_horizon_addon",
            "liquidation_worst_sector",
            "liquidation_worst_sector_loss",
        ),
    ),
    Stage(
        "pnl",
        "P&L explain",
        "Day-on-day P&L waterfall with its sensitivity challenger (MR-007); needs a previous market "
        "snapshot.",
        ("pnl_steps", "pnl_by_trade", "pnl_challenger"),
        (),
        ("pnl_total", "pnl_steps"),
    ),
    Stage(
        "regulatory",
        "Regulatory capital",
        "FRTB SA and IMA, SA-CCR, SIMM, BA-CVA and the cash ladder from the stored valuation and "
        "sensitivities (REG records).",
        ("reg_*",),
        ("counterparty",),
        ("regulatory",),
        faces=("bank",),
    ),
    Stage(
        "counterparty",
        "Counterparty exposure",
        "Exposure simulation, collateral, CVA and DVA, wrong-way risk (CR-001 to CR-004).",
        ("cp_*",),
        ("limits",),
        ("counterparty",),
    ),
    Stage(
        "fund",
        "Fund measures",
        "Leverage, margin, factor exposures, redemption stress, attribution and crowding (HF records).",
        ("fund_*",),
        ("limits",),
        ("fund",),
        faces=("fund",),
    ),
)
STAGE_BY_NAME: dict[str, Stage] = {s.name: s for s in STAGES}


class _Inputs:
    """Everything a stage needs, rebuilt from the parent run's stored snapshots. Upstream
    results are recomputed on demand and cached for the duration of the re-run."""

    def __init__(self, repo: DuckDBRepository, parent: RunRecord) -> None:
        self.repo, self.parent = repo, parent
        self.settings = get_settings()
        c = {k: v for k, v in parent.config.items() if k != "rerun"}
        self.cfg = EODConfig(
            firm_id=c.get("firm_id", "GMB"),
            var=VaRConfig(**c.get("var", {})),
            var_measures=c.get("var_measures"),
            include_historical_stress=c.get("include_historical_stress", True),
            stress_scenarios=c.get("stress_scenarios"),
            pnl_abs_tolerance=c.get("pnl_abs_tolerance", 50_000.0),
            counterparty=c.get("counterparty"),
            regulatory=c.get("regulatory"),
            exposure_paths=c.get("exposure_paths"),
            proxies=c.get("proxies"),
            stress_action_rules=c.get("stress_action_rules"),
            stress_funding_rules=c.get("stress_funding_rules"),
            liquidation_stress=c.get("liquidation_stress", True),
        )
        self.reporting = parent.reporting_currency
        self.market = repo.load_run_market(parent.run_id)
        self.snapshot = repo.load_portfolio_snapshot(parent.portfolio_snapshot_id)
        self.org = repo.load_organisation(self.cfg.firm_id)
        self.universe_list = repo.load_risk_factors()
        self.universe = {f.factor_id: f for f in self.universe_list}

    @property
    def is_fund(self) -> bool:
        return self.org.firm.firm_type == "HEDGE_FUND"

    @cached_property
    def history(self) -> MarketHistory:
        return MarketHistory.from_long(self.repo.load_market_history())

    @cached_property
    def prev_market(self):
        pid = self.parent.previous_market_snapshot_id
        return self.repo.load_market_snapshot(pid) if pid else None

    @cached_property
    def collateral(self) -> CollateralMap:
        return CollateralMap.from_reference(*self.repo.load_netting_sets())

    @cached_property
    def val(self):
        return value_portfolio(
            self.snapshot, self.market, self.org, self.reporting, collateral=self.collateral
        )

    @cached_property
    def pf(self) -> Portfolio:
        return Portfolio(
            self.snapshot.trades,
            self.market,
            self.reporting,
            universe=self.universe,
            collateral=self.collateral,
        )

    @cached_property
    def sens(self) -> pd.DataFrame:
        return compute_sensitivities(self.pf)

    @cached_property
    def measures(self) -> list[VaRMeasure]:
        """The measures the parent run produced (recorded in its config), so the re-run
        reproduces the same matrix whatever the setup says today."""
        stored = self.cfg.var_measures
        return [VaRMeasure.from_dict(m) for m in stored] if stored else list(DEFAULT_MEASURES)

    @cached_property
    def var_set(self) -> MeasureSet:
        return compute_measures(self.pf, self.sens, self.history, self.measures, self.cfg.var)

    @property
    def hs(self):
        return self.var_set.headline.result

    @cached_property
    def stress_run(self):
        """The definitions the parent ran (recorded in its config, OPS-005); a run made before
        the stress setup existed ran the built-in library."""
        recorded = self.cfg.stress_scenarios
        stored = self.repo.load_market_episodes()
        if recorded is None:
            windows = episode_windows(self.history, stored)
            legacy = built_in_definitions(list(windows), legacy=True)  # what it ran
            recorded = [{**d.to_dict(), "review_due": None} for d in legacy]
        return run_stress_definitions(
            self.pf, self.history, recorded, stored, self.cfg.include_historical_stress
        )

    @property
    def stress(self) -> list:
        return self.stress_run.results

    @property
    def limit_stress(self) -> list:
        return self.stress_run.limit_results


def _stage_valuation(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    repo.save_run_frame(rid, "valuation", x.val.table)
    return {"pv": x.pf.total_pv, "priced_trades": len(x.pf.priced_ids), "unpriced_trades": len(x.pf.errors)}


def _stage_sensitivities(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    repo.save_run_frame(rid, "sensitivities", x.sens)
    return {}


def _stage_var(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    persist_var(repo, rid, x.var_set, runs_dir)
    return x.var_set.summary()


def _stage_backtest(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    conf = x.var_set.headline.measure.confidence
    bts = static_backtest(x.hs.portfolio_pnl, conf)
    btl = live_backtest(repo.list_runs(run_type="EOD", limit=1000), conf)
    bth = history_backtest(x.pf, x.sens, x.history, x.market.as_of, conf)
    persist_backtest(repo, rid, bts, btl, bth)
    return {"backtest_zone": bts.zone, "backtest_exceptions": bts.exceptions, "backtest_days": bts.days}


def _stage_factor_stress(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    fs = run_factor_stress(x.pf, x.sens, x.history, x.val.table)
    persist_factor_stress(repo, rid, fs)
    return fs.summary()


def _stage_stress_explain(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    ex = run_stress_explain(x.pf, x.sens, x.stress, x.limit_stress, x.val.table, x.hs)
    persist_stress_explain(repo, rid, ex)
    return ex.summary()


def _pca_stress(x: _Inputs, reverse: bool, monte_carlo: bool):
    stored = x.repo.load_limits(on=x.market.as_of)
    limits, _ = effective_limits(stored, x.repo.load_increases(status="APPROVED"), x.market.as_of)
    return run_pca_stress(
        x.pf,
        x.sens,
        x.history,
        x.val.table,
        limits,
        episode_windows(x.history, x.repo.load_market_episodes()),
        reverse,
        monte_carlo,
    )


def _stage_reverse_stress(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    res = _pca_stress(x, True, False).reverse
    persist_reverse_stress(repo, rid, res)  # type: ignore[arg-type]
    return res.summary()  # type: ignore[union-attr]


def _stage_stress_monte_carlo(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    res = _pca_stress(x, False, True).monte_carlo
    persist_stress_mc(repo, rid, res)  # type: ignore[arg-type]
    return res.summary()  # type: ignore[union-attr]


def _stage_stress_paths(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    from novera.workflows.stress_paths import rules_for_run

    stored = x.repo.load_limits(on=x.market.as_of)
    limits, _ = effective_limits(stored, x.repo.load_increases(status="APPROVED"), x.market.as_of)
    far = {
        t.trade_id
        for t in x.snapshot.trades
        if t.product_type.value == "COMMODITY_FUTURE"
        and (t.instrument.expiry_date - x.market.as_of).days > 300
    }
    from novera.risk.funding_chain import FundingRules
    from novera.risk.stress_paths import HORIZON
    from novera.workflows.stress_paths import funding_inputs, funding_rules_for_run

    rules = x.cfg.stress_action_rules
    if rules is None:  # a run made before the stress paths existed: today's rules
        rules = [r.to_dict() for r in rules_for_run(repo)]
    funding_rules = x.cfg.stress_funding_rules
    if funding_rules is None:  # a run made before the funding chain existed (round 52): today's rules
        funding_rules = funding_rules_for_run(repo).to_list()
    res = run_stress_paths_stage(
        x.pf,
        x.sens,
        x.history,
        x.val.table,
        liquidity(x.val.table, x.hs.var, far).by_trade,
        x.hs,
        limits,
        x.limit_stress,
        episode_windows(x.history, x.repo.load_market_episodes()),
        rules,
        workers=workers,
        funding=funding_inputs(
            x.repo,
            x.pf,
            x.sens,
            x.val.table,
            x.cfg.firm_id,
            FundingRules.from_list(funding_rules),
            x.settings.im_threshold,
            HORIZON,
        ),
        funding_rules=funding_rules,
        all_results=x.stress,
    )
    persist_stress_paths(repo, rid, res)
    return res.summary()


def _stage_liquidation_stress(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    from novera.workflows.liquidation_stress import persist_liquidation, run_stage
    from novera.workflows.stress_paths import rules_for_run

    far = {
        t.trade_id
        for t in x.snapshot.trades
        if t.product_type.value == "COMMODITY_FUTURE"
        and (t.instrument.expiry_date - x.market.as_of).days > 300
    }
    rules = x.cfg.stress_action_rules
    if rules is None:
        rules = [r.to_dict() for r in rules_for_run(repo)]
    res = run_stage(
        x.pf,
        x.sens,
        x.history,
        x.val.table,
        liquidity(x.val.table, x.hs.var, far).by_trade,
        x.stress,
        x.limit_stress,
        rules,
        episode_windows(x.history, x.repo.load_market_episodes()),
    )
    persist_liquidation(repo, rid, res)
    return res.summary()


def _stage_stress(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    persist_stress(repo, rid, x.stress, x.stress_run.recorded, x.stress_run.info)
    return stress_headline(x.stress, x.limit_stress, x.val.table)


def _stage_limits(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    as_of = x.market.as_of
    stored = repo.load_limits(on=as_of)
    base_amounts = {lim.limit_id: lim.amount for lim in stored}
    limits, increase_ids = effective_limits(stored, repo.load_increases(status="APPROVED"), as_of)
    pfe = None
    cps = repo.load_run_frame(x.parent.run_id, "cp_counterparty_summary")
    if len(cps):
        pfe = {str(r["counterparty_id"]): float(r["peak_pfe95"]) for _, r in cps.iterrows()}
    fm = x.parent.summary.get("fund") or None
    fund_metrics = (
        {k: fm[k] for k in ("gross_leverage", "margin_to_nav", "largest_pb_share") if k in fm} if fm else None
    )
    table = (
        monitor(
            limits,
            RiskInputs(
                x.val.table,
                x.sens,
                x.hs,
                x.limit_stress,
                counterparty_pfe=pfe,
                fund_metrics=fund_metrics,
                limit_var=x.var_set.limit_inputs(),
            ),
            as_of,
            base_amounts,
            increase_ids,
        )
        if limits
        else pd.DataFrame()
    )
    if len(table):
        repo.save_run_frame(rid, "limits", table)
    return {
        "limits_monitored": int(len(table)),
        "breaches": int((table["status"] == "BREACH").sum()) if len(table) else 0,
        "warnings": int((table["status"] == "WARNING").sum()) if len(table) else 0,
    }


def _stage_concentration(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    conc = concentration(x.val.table, x.hs.contributions, x.sens)
    far = {
        t.trade_id
        for t in x.snapshot.trades
        if t.product_type.value == "COMMODITY_FUTURE"
        and (t.instrument.expiry_date - x.market.as_of).days > 300
    }
    liq = liquidity(x.val.table, x.hs.var, far)
    lt = look_through(list(x.snapshot.trades), x.market, x.reporting)
    persist_concentration(repo, rid, conc, liq, lt)
    return {
        "liquidity_adjusted_var": liq.liquidity_adjusted_var,
        "liquidity_horizon_days": liq.horizon_days,
        "concentration_flags": len(conc.flags),
        "liquidity_flags": len(liq.flags),
        "lookthrough_flags": len(lt.flags),
        "fund_holdings": int(lt.holdings["trade_id"].nunique()) if len(lt.holdings) else 0,
    }


def _stage_pnl(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    if x.prev_market is None:
        raise ValueError("P&L explain needs a previous market snapshot; this run has none")
    prev_snapshot = repo.load_portfolio_snapshot_before(x.market.as_of)
    prev_trades = (
        list(prev_snapshot.trades)
        if prev_snapshot is not None
        else [t for t in x.snapshot.trades if t.trade_date < x.market.as_of]
    )
    prev_pf = Portfolio(prev_trades, x.prev_market, x.reporting, universe=x.universe, collateral=x.collateral)
    pnl = explain_pnl(x.pf, x.prev_market, prev_trades, compute_sensitivities(prev_pf))
    persist_pnl(repo, rid, pnl)
    return {
        "pnl_total": pnl.total,
        "pnl_steps": {r["step"]: float(r["pnl"]) for _, r in pnl.steps.iterrows()},
    }


def _stage_regulatory(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    return {"regulatory": run_regulatory(repo, rid, x.settings, runs_dir=runs_dir).summary()}


def _stage_counterparty(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    paths = x.cfg.exposure_paths or x.settings.exposure_paths
    cps = run_counterparty(
        repo, rid, ExposureSimConfig(paths=paths), x.settings, runs_dir=runs_dir, workers=workers
    ).counterparty_summary
    return {
        "counterparty": {
            "counterparties": int(len(cps)),
            "total_epe": float(cps["epe"].sum()),
            "total_cva": float(cps["cva"].sum()),
            "total_dva": float(cps["dva"].sum()),
            "largest_pfe95": {
                "counterparty_id": str(cps.iloc[0]["counterparty_id"]),
                "peak_pfe95": float(cps.iloc[0]["peak_pfe95"]),
            }
            if len(cps)
            else None,
            "wrong_way_flags": int(cps["wrong_way"].fillna(False).sum()) if "wrong_way" in cps else 0,
            "paths": paths,
        }
    }


def _stage_fund(x: _Inputs, repo, rid: str, runs_dir, workers) -> dict[str, Any]:
    return {"fund": run_fund(repo, rid, x.settings, runs_dir=runs_dir).summary()}


_STAGE_FUNCTIONS = {
    "valuation": _stage_valuation,
    "sensitivities": _stage_sensitivities,
    "var": _stage_var,
    "backtest": _stage_backtest,
    "stress": _stage_stress,
    "factor_stress": _stage_factor_stress,
    "stress_explain": _stage_stress_explain,
    "reverse_stress": _stage_reverse_stress,
    "stress_monte_carlo": _stage_stress_monte_carlo,
    "limits": _stage_limits,
    "concentration": _stage_concentration,
    "stress_paths": _stage_stress_paths,
    "liquidation_stress": _stage_liquidation_stress,
    "pnl": _stage_pnl,
    "regulatory": _stage_regulatory,
    "counterparty": _stage_counterparty,
    "fund": _stage_fund,
}


@dataclass
class RerunResult:
    run: RunRecord
    parent: RunRecord
    stage: Stage
    seconds: float
    changed: dict[str, dict[str, Any]]
    stale_stages: tuple[str, ...]
    copied_tables: list[str]


def rerun_stage(
    repo: DuckDBRepository,
    run_id: str,
    stage: str,
    actor: str,
    reason: str = "",
    runs_dir: Path | None = None,
    workers: int | None = None,
) -> RerunResult:
    """Re-run ``stage`` of the stored run ``run_id`` into a new RERUN run. Raises ValueError for
    an unknown stage, a stage that does not apply to the firm's face, or a parent that is neither
    COMPLETED nor PARTIAL. Re-running a stage that failed on a PARTIAL parent clears it; the
    re-run is PARTIAL only while other failed stages remain."""
    if stage not in STAGE_BY_NAME:
        raise ValueError(f"unknown stage {stage!r}; choose from {', '.join(STAGE_BY_NAME)}")
    spec = STAGE_BY_NAME[stage]
    parent = repo.load_run(run_id)
    if parent.status not in USABLE_STATUSES:
        raise ValueError(f"run {run_id} is {parent.status}; only a completed or partial run can be re-run")
    if workers is not None:
        os.environ["NOVERA_WORKERS"] = str(workers)
    x = _Inputs(repo, parent)
    face = "fund" if x.is_fund else "bank"
    if face not in spec.faces:
        raise ValueError(f"stage {stage!r} does not apply to the {face} face")

    sweep_stuck(repo)  # decision 36.3
    run = RunRecord(
        new_run_id(),
        "RERUN",
        parent.business_date,
        parent.portfolio_snapshot_id,
        parent.market_snapshot_id,
        parent.previous_market_snapshot_id,
        parent.reporting_currency,
        dict(MODEL_VERSIONS),
        {
            **{k: v for k, v in parent.config.items() if k != "rerun"},
            "rerun": {"parent_run_id": parent.run_id, "stage": stage, "actor": actor, "reason": reason},
        },
    )
    run.verdict = parent.verdict  # data quality is not recomputed
    run.summary = {**dict(parent.summary), "owner": owner()}
    events = [
        AuditEvent.now(
            actor, "RERUN_STARTED", run.run_id, parent_run_id=parent.run_id, stage=stage, reason=reason
        )
    ]
    t0 = time.perf_counter()
    copied = repo.copy_run_frames(parent.run_id, run.run_id)
    src = run_dir(parent.run_id, runs_dir)
    if any(src.iterdir()):
        shutil.copytree(src, run_dir(run.run_id, runs_dir), dirs_exist_ok=True)
    copy_seconds = time.perf_counter() - t0
    repo.save_run(run)  # the stored-run engines load the record by id
    before = {k: parent.summary.get(k) for k in spec.summary_keys}
    t1 = time.perf_counter()
    try:
        run.summary.update(_STAGE_FUNCTIONS[stage](x, repo, run.run_id, runs_dir, workers))
    except Exception as e:
        run.summary["rerun"] = {**run.config["rerun"], "error": f"{type(e).__name__}: {e}"[:300]}
        run.timings = {"copy": copy_seconds, stage: time.perf_counter() - t1}
        run.finish("FAILED")
        repo.save_run(run)
        events.append(
            AuditEvent.now(actor, "RERUN_FINISHED", run.run_id, status="FAILED", error=str(e)[:300])
        )
        repo.save_audit_events(events)
        raise
    seconds = time.perf_counter() - t1
    changed = {
        k: {"before": before[k], "after": run.summary.get(k)}
        for k in spec.summary_keys
        if k in parent.summary
        and before[k] != run.summary.get(k)  # keys a stage added later are new, not changed
    }
    run.summary["rerun"] = {
        **run.config["rerun"],
        "stale_stages": list(spec.dependents),
        "changed": changed,
        "seconds": seconds,
        "copied_tables": len(copied),
    }
    run.timings = {"copy": copy_seconds, stage: seconds}
    # Stages that failed on the parent stay missing unless this re-run recomputed them.
    still_failed = {k: v for k, v in parent.failed_stages.items() if k != stage}
    if still_failed:
        run.summary["failed_stages"] = still_failed
    else:
        run.summary.pop("failed_stages", None)
    run.finish("PARTIAL" if still_failed else "COMPLETED")
    events.append(
        AuditEvent.now(
            actor,
            "RERUN_FINISHED",
            run.run_id,
            status=run.status,
            stage=stage,
            seconds=seconds,
            changed=sorted(changed),
        )
    )
    repo.save_audit_events(events)
    repo.save_run(run)
    return RerunResult(run, parent, spec, seconds, changed, spec.dependents, copied)
