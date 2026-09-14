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
from novera.data_quality import (
    DataQualityReport,
    check_market_data,
    check_pnl_residuals,
    check_trades,
    check_valuation,
)
from novera.limits import RiskInputs, effective_limits, expire_increases, monitor, sync_breaches
from novera.market_data.history import MarketHistory
from novera.pricing import valuation as valuation_mod
from novera.pricing.valuation import value_portfolio
from novera.risk import (
    HYPOTHETICAL_LIBRARY,
    Portfolio,
    VaRConfig,
    compute_sensitivities,
    explain_pnl,
    historical_var,
    run_stress,
    stress_table,
    taylor_var,
)
from novera.risk import sensitivities as sens_mod
from novera.risk import stress as stress_mod
from novera.risk import var as var_mod
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
}


@dataclass
class EODConfig:
    firm_id: str = "GMB"
    var: VaRConfig = field(default_factory=VaRConfig)
    workers: int | None = None
    include_historical_stress: bool = True
    pnl_abs_tolerance: float = 50_000.0
    actor: str = "eod-scheduler"

    def as_dict(self) -> dict[str, Any]:
        return {
            "firm_id": self.firm_id,
            "var": self.var.__dict__,
            "include_historical_stress": self.include_historical_stress,
            "pnl_abs_tolerance": self.pnl_abs_tolerance,
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

    with _timed(timings, "valuation"):
        val = value_portfolio(snapshot, market, org, reporting)
        pf = Portfolio(snapshot.trades, market, reporting, universe=universe)
    with _timed(timings, "dq_pre"):
        dq.findings += check_market_data(market, universe_list, pf.index)
        dq.findings += check_trades(list(snapshot.trades), org, counterparties, netting_sets, market.as_of)
        dq.findings += check_valuation(val.table)
    with _timed(timings, "sensitivities"):
        sens = compute_sensitivities(pf)
    with _timed(timings, "var"):
        hs = historical_var(pf, history, cfg.var)
    with _timed(timings, "var_challenger"):
        tv = taylor_var(pf, sens, history, cfg.var)
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
    sync = sync_breaches(repo, run.run_id, market.as_of, limit_table) if persist else None
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
            _persist(repo, run, val.table, sens, hs, tv, stress, limit_table, dq, pnl, events, runs_dir)
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


def _persist(repo, run, valuation, sens, hs, tv, stress, limit_table, dq, pnl, events, runs_dir) -> None:
    rid = run.run_id
    repo.save_run(run)
    repo.save_run_frame(rid, "valuation", valuation)
    repo.save_run_frame(rid, "sensitivities", sens)
    var_summary = pd.DataFrame(
        [
            {
                "method": hs.method,
                "var": hs.var,
                "es": hs.es,
                "var_scaled": hs.var_scaled,
                "es_scaled": hs.es_scaled,
                "confidence": hs.config.confidence,
                "es_confidence": hs.config.es_confidence,
                "window_days": hs.config.window_days,
                "scenarios": len(hs.pnl),
                "var_scenario_date": hs.var_scenario_date,
            },
            {
                "method": tv.method,
                "var": tv.var,
                "es": tv.es,
                "var_scaled": tv.var_scaled,
                "es_scaled": tv.es_scaled,
                "confidence": tv.config.confidence,
                "es_confidence": tv.config.es_confidence,
                "window_days": tv.config.window_days,
                "scenarios": len(tv.pnl),
                "var_scenario_date": tv.var_scenario_date,
            },
        ]
    )
    repo.save_run_frame(rid, "var_summary", var_summary)
    repo.save_run_frame(rid, "var_contributions", hs.contributions.assign(method=hs.method))
    repo.save_run_frame(rid, "var_contributions_challenger", tv.contributions.assign(method=tv.method))
    scen = pd.DataFrame(
        {
            "scenario_date": hs.portfolio_pnl.index,
            "portfolio_pnl": hs.portfolio_pnl.to_numpy(),
            "challenger_pnl": tv.portfolio_pnl.reindex(hs.portfolio_pnl.index).to_numpy(),
        }
    )
    repo.save_run_frame(rid, "var_scenarios", scen)
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
    if len(limit_table):
        repo.save_run_frame(rid, "limits", limit_table)
    repo.save_run_frame(
        rid,
        "dq_findings",
        dq.table().assign(affected_trade_ids=[",".join(f.affected_trade_ids[:200]) for f in dq.findings]),
    )
    if pnl is not None:
        repo.save_run_frame(rid, "pnl_steps", pnl.steps)
        repo.save_run_frame(rid, "pnl_by_trade", pnl.by_trade)
        repo.save_run_frame(rid, "pnl_challenger", pnl.challenger)
    repo.save_audit_events(events)
    # Full scenario matrices go to Parquet next to the database (too wide for a table).
    base = runs_dir or get_settings().data_dir / "runs"
    d = Path(base) / rid
    d.mkdir(parents=True, exist_ok=True)
    hs.pnl.to_parquet(d / "var_pnl_full_revaluation.parquet")
    tv.pnl.to_parquet(d / "var_pnl_delta_gamma_vega.parquet")
