"""The end-of-day risk run: one governed pass from snapshots to stored, auditable results.

Steps (each timed and recorded on the run):
    load -> data-quality pre-checks -> valuation -> sensitivities -> VaR (+ challenger)
    -> stress -> limits -> P&L explain (+ challenger) -> data-quality post-checks
    -> verdict -> persist -> audit events
The run always completes; the verdict says whether the numbers can be trusted.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from novera.config import get_settings
from novera.counterparty_risk import ExposureSimConfig, run_counterparty
from novera.data_quality import (
    DataQualityReport,
    check_market_data,
    check_pnl_residuals,
    check_trades,
    check_valuation,
    proxy_findings,
)
from novera.fund import run_fund
from novera.limits import RiskInputs, effective_limits, expire_increases, monitor, sync_breaches
from novera.market_data.history import MarketHistory
from novera.market_data.proxies import MODEL_VERSION as PROXY_VERSION
from novera.market_data.proxies import apply_proxies
from novera.pricing import valuation as valuation_mod
from novera.pricing.valuation import value_portfolio
from novera.regulatory import run_regulatory
from novera.risk import (
    HYPOTHETICAL_LIBRARY,
    Portfolio,
    VaRConfig,
    compute_sensitivities,
    concentration,
    explain_pnl,
    historical_var,
    liquidity,
    live_backtest,
    look_through,
    monte_carlo_var,
    run_stress,
    static_backtest,
    stress_table,
    taylor_var,
)
from novera.risk import sensitivities as sens_mod
from novera.risk import stress as stress_mod
from novera.risk import var as var_mod
from novera.risk.backtest import MODEL_VERSION as BACKTEST_VERSION
from novera.risk.concentration import MODEL_VERSION as CONC_VERSION
from novera.risk.liquidity import MODEL_VERSION as LIQ_VERSION
from novera.risk.lookthrough import MODEL_VERSION as LOOKTHROUGH_VERSION
from novera.risk.monte_carlo import MODEL_VERSION as MC_VERSION
from novera.risk.pnl_attribution import MODEL_VERSION as PNL_VERSION
from novera.risk.stress import historical_episodes_from_simulation
from novera.simulation.market_data import DEFAULT_EPISODES
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.alerts import alerts_from_run, channels_from_settings, dispatch
from novera.workflows.runs import AuditEvent, RunRecord, new_run_id

MODEL_VERSIONS = {
    "valuation": valuation_mod.VALUATION_VERSION,
    "sensitivities": sens_mod.MODEL_VERSION,
    "var": var_mod.MODEL_VERSION,
    "stress": stress_mod.MODEL_VERSION,
    "pnl_attribution": PNL_VERSION,
    "monte_carlo": MC_VERSION,
    "backtest": BACKTEST_VERSION,
    "concentration": CONC_VERSION,
    "liquidity": LIQ_VERSION,
    "lookthrough": LOOKTHROUGH_VERSION,
    "market_data_proxies": PROXY_VERSION,
}


@dataclass
class EODConfig:
    firm_id: str = "GMB"
    var: VaRConfig = field(default_factory=VaRConfig)
    workers: int | None = None
    include_historical_stress: bool = True
    pnl_abs_tolerance: float = 50_000.0
    actor: str = "eod-scheduler"
    counterparty: bool | None = None  # None: follow settings.exposure_enabled
    regulatory: bool | None = None  # None: follow settings.regulatory_enabled
    exposure_paths: int | None = None  # None: settings.exposure_paths
    proxies: bool | None = None  # None: follow settings.proxies_enabled

    def as_dict(self) -> dict[str, Any]:
        return {
            "firm_id": self.firm_id,
            "var": self.var.__dict__,
            "include_historical_stress": self.include_historical_stress,
            "pnl_abs_tolerance": self.pnl_abs_tolerance,
            "counterparty": self.counterparty,
            "regulatory": self.regulatory,
            "exposure_paths": self.exposure_paths,
            "proxies": self.proxies,
        }


@dataclass
class EODResult:
    run: RunRecord
    valuation: pd.DataFrame
    sensitivities: pd.DataFrame
    var: Any
    challenger_var: Any
    stress: list
    limits: pd.DataFrame
    dq: DataQualityReport
    pnl: Any
    events: list[AuditEvent]
    sync: Any = None  # breach sync outcome when persisted


def _timed(timings: dict[str, float], name: str):
    class _T:
        def __enter__(self):
            self.t0 = time.perf_counter()

        def __exit__(self, *exc):
            timings[name] = round(time.perf_counter() - self.t0, 3)

    return _T()


def run_eod(
    repo: DuckDBRepository,
    cfg: EODConfig | None = None,
    business_date: date | None = None,
    persist: bool = True,
    runs_dir: Path | None = None,
) -> EODResult:
    cfg = cfg or EODConfig()
    settings = get_settings()
    reporting = settings.reporting_currency
    if cfg.workers is not None:
        os.environ["NOVERA_WORKERS"] = str(cfg.workers)
    timings: dict[str, float] = {}
    events: list[AuditEvent] = []

    with _timed(timings, "load"):
        repo.init_schema()  # idempotent: adds run tables to databases created before them
        psnaps = repo.list_portfolio_snapshots()
        msnaps = repo.list_market_snapshots()
        if not psnaps or not msnaps:
            raise RuntimeError("no snapshots stored; run the simulator first")
        bd = business_date or msnaps[-1][1]
        market_id = next(sid for sid, d, _ in reversed(msnaps) if d <= bd)
        market = repo.load_market_snapshot(market_id)
        prev_ids = [sid for sid, d, _ in msnaps if d < market.as_of]
        prev_market = repo.load_market_snapshot(prev_ids[-1]) if prev_ids else None
        portfolio_id = next(sid for sid, d, _ in reversed(psnaps) if d <= bd)
        snapshot = repo.load_portfolio_snapshot(portfolio_id)
        org = repo.load_organisation(cfg.firm_id)
        universe_list = repo.load_risk_factors()
        universe = {f.factor_id: f for f in universe_list}
        history = MarketHistory.from_long(repo.load_market_history())
        limits = repo.load_limits(on=market.as_of)
        events += expire_increases(repo, market.as_of)
        limits, increase_ids = effective_limits(limits, repo.load_increases(status="APPROVED"), market.as_of)
        base_amounts = {lim.limit_id: lim.amount for lim in repo.load_limits(on=market.as_of)}
        prev_snapshot = repo.load_portfolio_snapshot_before(market.as_of)
        counterparties = {c.counterparty_id for c in repo.load_counterparties()}
        netting_sets = {n.netting_set_id for n in repo.load_netting_sets()[0]}

    run = RunRecord(
        new_run_id(),
        "EOD",
        market.as_of,
        snapshot.snapshot_id,
        market.snapshot_id,
        prev_market.snapshot_id if prev_market else None,
        reporting,
        MODEL_VERSIONS,
        cfg.as_dict(),
    )
    events.append(
        AuditEvent.now(
            cfg.actor,
            "RUN_STARTED",
            run.run_id,
            business_date=str(market.as_of),
            portfolio_snapshot_id=snapshot.snapshot_id,
            market_snapshot_id=market.snapshot_id,
        )
    )
    dq = DataQualityReport()

    raw_market = market
    proxies = None
    if cfg.proxies if cfg.proxies is not None else settings.proxies_enabled:
        proxies = apply_proxies(raw_market, universe_list, prev_market)
        market = proxies.market
    with _timed(timings, "valuation"):
        val = value_portfolio(snapshot, market, org, reporting)
        pf = Portfolio(snapshot.trades, market, reporting, universe=universe)
    with _timed(timings, "dq_pre"):
        dq.findings += check_market_data(raw_market, universe_list, pf.index)
        if proxies is not None:
            dq.findings += proxy_findings(proxies, pf.index)
        dq.findings += check_trades(list(snapshot.trades), org, counterparties, netting_sets, market.as_of)
        dq.findings += check_valuation(val.table)
    with _timed(timings, "sensitivities"):
        sens = compute_sensitivities(pf)
    with _timed(timings, "var"):
        hs = historical_var(pf, history, cfg.var)
    with _timed(timings, "var_challenger"):
        tv = taylor_var(pf, sens, history, cfg.var)
    with _timed(timings, "monte_carlo"):
        mc = monte_carlo_var(pf, sens, history, cfg.var)
    with _timed(timings, "backtest"):
        bt_static = static_backtest(hs.portfolio_pnl, cfg.var.confidence)
        bt_live = live_backtest(repo.list_runs(run_type="EOD", limit=1000) + [], cfg.var.confidence)
    with _timed(timings, "stress"):
        scenarios = list(HYPOTHETICAL_LIBRARY)
        if cfg.include_historical_stress:
            scenarios += historical_episodes_from_simulation(history, DEFAULT_EPISODES, market.as_of)
        stress = run_stress(pf, scenarios, history)
    with _timed(timings, "limits"):
        limit_table = (
            monitor(limits, RiskInputs(val.table, sens, hs, stress), market.as_of, base_amounts, increase_ids)
            if limits
            else pd.DataFrame()
        )
    with _timed(timings, "concentration"):
        conc = concentration(val.table, hs.contributions, sens)
        far = {
            t.trade_id
            for t in snapshot.trades
            if t.product_type.value == "COMMODITY_FUTURE"
            and (t.instrument.expiry_date - market.as_of).days > 300
        }
        liq = liquidity(val.table, hs.var, far)
        lookthrough = look_through(list(snapshot.trades), market, reporting)
    with _timed(timings, "pnl"):
        pnl = None
        if prev_market is not None:
            prev_trades = (
                list(prev_snapshot.trades)
                if prev_snapshot is not None
                else [t for t in snapshot.trades if t.trade_date < market.as_of]
            )
            prev_pf = Portfolio(prev_trades, prev_market, reporting, universe=universe)
            prev_sens = compute_sensitivities(prev_pf)
            pnl = explain_pnl(pf, prev_market, prev_trades, prev_sens)
            dq.findings += check_pnl_residuals(pnl.challenger, cfg.pnl_abs_tolerance)

    # Verdict and audit events.
    run.verdict = dq.verdict
    for f in dq.findings:
        events.append(
            AuditEvent.now(
                "data-quality",
                "DQ_FINDING",
                run.run_id,
                code=f.code,
                severity=f.severity,
                finding_subject=f.subject,
                affected_trades=len(f.affected_trade_ids),
            )
        )
    breaches = warnings = 0
    if len(limit_table):
        for _, r in limit_table[limit_table["status"].isin(["BREACH", "WARNING"])].iterrows():
            kind = "LIMIT_BREACH" if r["status"] == "BREACH" else "LIMIT_WARNING"
            events.append(
                AuditEvent.now(
                    "limit-monitor",
                    kind,
                    r["limit_id"],
                    run_id=run.run_id,
                    utilisation=float(r["utilisation"]),
                    current=float(r["current"]),
                    amount=float(r["amount"]),
                    owner=r["owner"],
                )
            )
        breaches = int((limit_table["status"] == "BREACH").sum())
        warnings = int((limit_table["status"] == "WARNING").sum())
    # Limits fed by the counterparty or fund engines are synced after those engines run, so the
    # first pass neither raises them on stale inputs nor closes them for lack of a measure.
    is_fund = org.firm.firm_type == "HEDGE_FUND"
    do_cp = settings.exposure_enabled if cfg.counterparty is None else cfg.counterparty
    deferred_types: set[str] = set()
    if persist and do_cp:
        deferred_types.add("COUNTERPARTY_EXPOSURE")
    if persist and is_fund:
        deferred_types |= {"LEVERAGE", "MARGIN_USAGE", "PB_CONCENTRATION"}
    first_pass = (
        limit_table[~limit_table["limit_type"].isin(deferred_types)] if len(limit_table) else limit_table
    )
    sync = sync_breaches(repo, run.run_id, market.as_of, first_pass) if persist else None
    if sync is not None:
        events += sync.events
    st = stress_table(stress, val.table)
    worst = st.iloc[0] if len(st) else None
    run.summary = {
        "pv": pf.total_pv,
        "priced_trades": len(pf.priced_ids),
        "unpriced_trades": len(pf.errors),
        "var": hs.var,
        "es": hs.es,
        "var_scaled": hs.var_scaled,
        "var_scenario_date": str(hs.var_scenario_date),
        "challenger_var": tv.var,
        "monte_carlo_var": mc.var,
        "monte_carlo_es": mc.es,
        "backtest_zone": bt_static.zone,
        "backtest_exceptions": bt_static.exceptions,
        "backtest_days": bt_static.days,
        "liquidity_adjusted_var": liq.liquidity_adjusted_var,
        "liquidity_horizon_days": liq.horizon_days,
        "concentration_flags": len(conc.flags),
        "liquidity_flags": len(liq.flags),
        "lookthrough_flags": len(lookthrough.flags),
        "fund_holdings": int(lookthrough.holdings["trade_id"].nunique()) if len(lookthrough.holdings) else 0,
        "worst_stress_id": worst["scenario_id"] if worst is not None else None,
        "worst_stress_name": worst["name"] if worst is not None else None,
        "worst_stress": float(worst["total"]) if worst is not None else None,
        "limits_monitored": int(len(limit_table)),
        "breaches": breaches,
        "warnings": warnings,
        "breaches_raised": len(sync.raised) if sync else 0,
        "breaches_auto_escalated": len(sync.auto_escalated) if sync else 0,
        "breaches_back_within_limit": len(sync.back_within_limit) if sync else 0,
        "dq_findings": len(dq.findings),
        "dq_verdict": dq.verdict,
        "md_proxies": len([a for a in proxies.actions if a.kind != "KEPT_STALE"]) if proxies else 0,
        "pnl_total": pnl.total if pnl else None,
        "pnl_steps": {r["step"]: float(r["pnl"]) for _, r in pnl.steps.iterrows()} if pnl else None,
    }
    run.timings = timings
    run.finish("COMPLETED")
    events.append(
        AuditEvent.now(
            cfg.actor,
            "RUN_FINISHED",
            run.run_id,
            status=run.status,
            verdict=run.verdict,
            var=hs.var,
            breaches=breaches,
        )
    )

    result = EODResult(run, val.table, sens, hs, tv, stress, limit_table, dq, pnl, events, sync)
    if persist:
        with _timed(timings, "persist"):
            _persist(
                repo,
                run,
                val.table,
                sens,
                hs,
                tv,
                stress,
                limit_table,
                dq,
                pnl,
                events,
                runs_dir,
                extras={
                    "mc": mc,
                    "bt_static": bt_static,
                    "bt_live": bt_live,
                    "conc": conc,
                    "liq": liq,
                    "proxies": proxies,
                    "lookthrough": lookthrough,
                },
            )
        pfe: dict[str, float] | None = None
        fund_metrics: dict[str, float] | None = None
        do_reg = (settings.regulatory_enabled if cfg.regulatory is None else cfg.regulatory) and not is_fund
        if do_reg:
            with _timed(timings, "regulatory"):
                reg = run_regulatory(repo, run.run_id, settings, runs_dir=runs_dir)
                run.summary["regulatory"] = reg.summary()
        if do_cp:
            with _timed(timings, "counterparty"):
                cp_run = run_counterparty(
                    repo,
                    run.run_id,
                    ExposureSimConfig(paths=cfg.exposure_paths or settings.exposure_paths),
                    settings,
                    runs_dir=runs_dir,
                    workers=cfg.workers,
                )
                cps = cp_run.counterparty_summary
                run.summary["counterparty"] = {
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
                    "paths": cfg.exposure_paths or settings.exposure_paths,
                }
                pfe = {str(r["counterparty_id"]): float(r["peak_pfe95"]) for _, r in cps.iterrows()}
        if is_fund:
            with _timed(timings, "fund"):
                fr = run_fund(repo, run.run_id, settings, runs_dir=runs_dir)
                run.summary["fund"] = fr.summary()
                fund_metrics = {
                    k: fr.summary()[k] for k in ("gross_leverage", "margin_to_nav", "largest_pb_share")
                }
        if pfe is not None or fund_metrics is not None:
            # Re-monitor with the engine-based measures and refresh the stored limits table.
            limit_table = (
                monitor(
                    limits,
                    RiskInputs(val.table, sens, hs, stress, counterparty_pfe=pfe, fund_metrics=fund_metrics),
                    market.as_of,
                    base_amounts,
                    increase_ids,
                )
                if limits
                else pd.DataFrame()
            )
            if len(limit_table):
                repo.save_run_frame(run.run_id, "limits", limit_table)
                run.summary["breaches"] = int((limit_table["status"] == "BREACH").sum())
                run.summary["warnings"] = int((limit_table["status"] == "WARNING").sum())
                second_pass = limit_table[limit_table["limit_type"].isin(deferred_types)]
                sync2 = sync_breaches(repo, run.run_id, market.as_of, second_pass)
                if sync2.events:
                    repo.save_audit_events(sync2.events)  # first-pass events are already stored
                events += sync2.events
                if sync is not None:
                    sync.raised += sync2.raised
                    sync.auto_escalated += sync2.auto_escalated
                    sync.back_within_limit += sync2.back_within_limit
                    sync.events += sync2.events
        if settings.alerts_enabled:
            with _timed(timings, "alerts"):
                sent = dispatch(repo, alerts_from_run(result, sync), channels_from_settings(settings))
            run.summary["alerts"] = {
                s_: sum(1 for a in sent if a.status == s_)
                for s_ in ("STORED", "SENT", "PARTIAL", "FAILED", "SUPPRESSED")
            }
        run.timings = timings
        repo.save_run(run)
    return result


def run_dir(run_id: str, runs_dir: Path | None = None) -> Path:
    """Directory for a run's Parquet matrices next to the database (too wide for a table)."""
    base = runs_dir or get_settings().data_dir / "runs"
    d = Path(base) / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _var_row(x: Any) -> dict[str, Any]:
    return {
        "method": x.method,
        "var": x.var,
        "es": x.es,
        "var_scaled": x.var_scaled,
        "es_scaled": x.es_scaled,
        "confidence": x.config.confidence,
        "es_confidence": x.config.es_confidence,
        "window_days": x.config.window_days,
        "scenarios": len(x.pnl),
        "var_scenario_date": x.var_scenario_date,
    }


def persist_var(repo, rid: str, hs, tv, mc, runs_dir: Path | None = None) -> None:
    """VaR tables: one summary row per method, contributions, the scenario vector, and the
    full P&L matrices as Parquet."""
    rows = [_var_row(hs), _var_row(tv)] + ([_var_row(mc)] if mc is not None else [])
    repo.save_run_frame(rid, "var_summary", pd.DataFrame(rows))
    repo.save_run_frame(rid, "var_contributions", hs.contributions.assign(method=hs.method))
    repo.save_run_frame(rid, "var_contributions_challenger", tv.contributions.assign(method=tv.method))
    if mc is not None:
        repo.save_run_frame(rid, "var_contributions_monte_carlo", mc.contributions.assign(method=mc.method))
    scen = pd.DataFrame(
        {
            "scenario_date": hs.portfolio_pnl.index,
            "portfolio_pnl": hs.portfolio_pnl.to_numpy(),
            "challenger_pnl": tv.portfolio_pnl.reindex(hs.portfolio_pnl.index).to_numpy(),
        }
    )
    repo.save_run_frame(rid, "var_scenarios", scen)
    d = run_dir(rid, runs_dir)
    hs.pnl.to_parquet(d / "var_pnl_full_revaluation.parquet")
    tv.pnl.to_parquet(d / "var_pnl_delta_gamma_vega.parquet")


def persist_stress(repo, rid: str, stress: list) -> None:
    stress_rows = pd.concat(
        [
            r.pnl.rename("pnl")
            .reset_index()
            .rename(columns={"index": "trade_id"})
            .assign(scenario_id=r.scenario.scenario_id)
            for r in stress
        ],
        ignore_index=True,
    )
    repo.save_run_frame(rid, "stress", stress_rows[["scenario_id", "trade_id", "pnl"]])
    repo.save_run_frame(
        rid,
        "stress_summary",
        pd.DataFrame(
            [
                {
                    "scenario_id": r.scenario.scenario_id,
                    "name": r.scenario.name,
                    "kind": r.scenario.kind,
                    "description": r.scenario.description,
                    "total": r.total,
                }
                for r in stress
            ]
        ),
    )


def persist_backtest(repo, rid: str, bts, btl) -> None:
    repo.save_run_frame(rid, "backtest_summary", pd.DataFrame([bts.summary(), btl.summary()]))
    repo.save_run_frame(rid, "backtest_series", bts.series.assign(kind=bts.kind))
    if len(btl.series):
        repo.save_run_frame(rid, "backtest_live_series", btl.series.assign(kind=btl.kind))


def persist_concentration(repo, rid: str, conc, liq, lt) -> None:
    if lt is not None:
        repo.save_run_frame(rid, "lookthrough_holdings", lt.holdings)
        repo.save_run_frame(rid, "lookthrough_constituents", lt.constituents)
    repo.save_run_frame(rid, "concentration", conc.by_dimension)
    repo.save_run_frame(rid, "concentration_top", conc.top_positions)
    repo.save_run_frame(rid, "concentration_tenor", conc.tenor)
    repo.save_run_frame(
        rid,
        "liquidity_trades",
        liq.by_trade.assign(horizon_bucket=lambda d: d["horizon_bucket"].astype(str)),
    )
    repo.save_run_frame(
        rid,
        "liquidity_buckets",
        liq.by_bucket.assign(horizon_bucket=lambda d: d["horizon_bucket"].astype(str)),
    )
    repo.save_run_frame(rid, "liquidity_desks", liq.by_desk)
    repo.save_run_frame(
        rid,
        "risk_flags",
        pd.DataFrame(
            [{"kind": "CONCENTRATION", "message": f} for f in conc.flags]
            + [{"kind": "LIQUIDITY", "message": f} for f in liq.flags]
            + [{"kind": "LOOKTHROUGH", "message": f} for f in (lt.flags if lt is not None else [])],
            columns=["kind", "message"],
        ),
    )


def persist_pnl(repo, rid: str, pnl) -> None:
    repo.save_run_frame(rid, "pnl_steps", pnl.steps)
    repo.save_run_frame(rid, "pnl_by_trade", pnl.by_trade)
    repo.save_run_frame(rid, "pnl_challenger", pnl.challenger)


def _persist(
    repo, run, valuation, sens, hs, tv, stress, limit_table, dq, pnl, events, runs_dir, extras=None
) -> None:
    rid = run.run_id
    repo.save_run(run)
    mc = None
    if extras:
        mc = extras["mc"]
        if extras.get("proxies") is not None:
            repo.save_run_frame(rid, "md_proxies", extras["proxies"].table())
        persist_backtest(repo, rid, extras["bt_static"], extras["bt_live"])
        persist_concentration(repo, rid, extras["conc"], extras["liq"], extras.get("lookthrough"))
    repo.save_run_frame(rid, "valuation", valuation)
    repo.save_run_frame(rid, "sensitivities", sens)
    persist_var(repo, rid, hs, tv, mc, runs_dir)
    persist_stress(repo, rid, stress)
    if len(limit_table):
        repo.save_run_frame(rid, "limits", limit_table)
    repo.save_run_frame(
        rid,
        "dq_findings",
        dq.table().assign(affected_trade_ids=[",".join(f.affected_trade_ids[:200]) for f in dq.findings]),
    )
    if pnl is not None:
        persist_pnl(repo, rid, pnl)
    repo.save_audit_events(events)
