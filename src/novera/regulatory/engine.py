"""Regulatory capital run for a stored market-risk run: FRTB SA and IMA, SA-CCR, SIMM-lite
initial margin, BA-CVA, funding cash ladder, and capital attributed to desk and business."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from novera.config import Settings, get_settings
from novera.market_data.history import MarketHistory
from novera.regulatory.cash_ladder import cash_ladder
from novera.regulatory.cva_capital import ba_cva
from novera.regulatory.frtb_ima import IMAResult, frtb_ima
from novera.regulatory.frtb_sa import FRTBSAResult, frtb_sa
from novera.regulatory.saccr import SACCRResult, saccr
from novera.regulatory.simm import SIMMResult, simm
from novera.risk.revaluation import Portfolio
from novera.storage.duckdb_repository import DuckDBRepository

MODEL_VERSION = "1.0.0"


@dataclass
class RegulatoryRun:
    run_id: str
    frtb_sa: FRTBSAResult
    frtb_ima: IMAResult
    saccr: SACCRResult
    simm: SIMMResult
    ba_cva_capital: float
    ba_cva_detail: pd.DataFrame
    cash_ladder: pd.DataFrame
    capital: pd.DataFrame  # component table
    by_desk: pd.DataFrame
    notes: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "frtb_sa": self.frtb_sa.total,
            "frtb_sa_sbm": self.frtb_sa.sbm,
            "frtb_sa_drc": self.frtb_sa.drc,
            "frtb_ima": self.frtb_ima.capital,
            "imes": self.frtb_ima.imes,
            "ses": self.frtb_ima.ses,
            "ima_multiplier": self.frtb_ima.multiplier,
            "nmrf": len(self.frtb_ima.nmrf),
            "saccr_ead": self.saccr.total_ead,
            "saccr_rwa": self.saccr.total_rwa,
            "saccr_capital": self.saccr.total_rwa * 0.08,
            "simm_im": self.simm.total,
            "ba_cva_capital": self.ba_cva_capital,
            "pla_red_desks": int((self.frtb_ima.pla["zone"] == "RED").sum()) if len(self.frtb_ima.pla) else 0,
        }


def run_regulatory(
    repo: DuckDBRepository,
    run_id: str,
    settings: Settings | None = None,
    persist: bool = True,
    runs_dir: Path | None = None,
) -> RegulatoryRun:
    settings = settings or get_settings()
    run = repo.load_run(run_id)
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    market = repo.load_market_snapshot(run.market_snapshot_id)
    universe = {f.factor_id: f for f in repo.load_risk_factors()}
    history = MarketHistory.from_long(repo.load_market_history())
    val = repo.load_run_frame(run_id, "valuation")
    sens = repo.load_run_frame(run_id, "sensitivities")
    netting_sets, csas = repo.load_netting_sets()
    counterparties = {c.counterparty_id: c for c in repo.load_counterparties()}
    reporting = run.reporting_currency
    base_vols = {
        u: market.vol_surface(u).atm(0.25)
        for u in {f.split(":")[1] for f in market.factors_with_prefix("VOL:")}
    }
    # Valuation lacks netting_set_id; add it from the snapshot for SIMM.
    ns_by_trade = {t.trade_id: t.netting_set_id for t in snap.trades}
    val = val.assign(netting_set_id=val["trade_id"].map(ns_by_trade))

    sa = frtb_sa(sens, val, base_vols)
    # IMA needs the full-revaluation scenario P&L (hypothetical P&L) per trade.
    base = Path(runs_dir or settings.data_dir / "runs") / run_id
    hpl_path = base / "var_pnl_full_revaluation.parquet"
    hpl = pd.read_parquet(hpl_path) if hpl_path.exists() else pd.DataFrame()
    dq = repo.load_run_frame(run_id, "dq_findings")
    nmrf = []
    if len(dq):
        for _, r in dq[dq["code"].isin(["MD_MISSING_FACTOR", "MD_STALE_FACTOR"])].iterrows():
            subj = str(r["subject"])
            nmrf += [f for f in market.values if f == subj or (subj.endswith(":") and f.startswith(subj))]
            if subj not in market.values and not subj.endswith(":"):
                nmrf.append(subj)
    bt = repo.load_run_frame(run_id, "backtest_summary")
    exceptions = int(bt[bt["kind"] == "STATIC_HYPOTHETICAL"]["exceptions"].iloc[0]) if len(bt) else 0
    pf = Portfolio(snap.trades, market, reporting, universe=universe)
    ima = frtb_ima(
        pf,
        sens,
        history,
        val,
        hpl,
        sorted(set(nmrf)),
        exceptions,
        int(run.config.get("var", {}).get("window_days", 500)),
    )
    im = simm(sens, val, base_vols)
    # Collateral held today per netting set (from the exposure engine's t0 balance if stored).
    prof = repo.load_run_frame(run_id, "cp_profiles")
    coll = {}
    if len(prof):
        first = prof[prof["step"] == prof["step"].iloc[0]]
        coll = {r["netting_set_id"]: float(r["mean_collateral"]) for _, r in first.iterrows()}
    sc = saccr(list(snap.trades), market, netting_sets, csas, counterparties, reporting, market.as_of, coll)
    maturities: dict[str, float] = {}
    if len(sc.trades):
        # Effective maturity proxy: EAD-weighted average of the add-on maturity factors is not
        # available per counterparty; use 1y for margined, 2.5y for unmargined sets.
        for _, r in sc.by_netting_set.iterrows():
            maturities[r["counterparty_id"]] = max(
                maturities.get(r["counterparty_id"], 0.0), 1.0 if r["margined"] else 2.5
            )
    cva_k, cva_detail = (
        ba_cva(sc.by_counterparty, maturities, counterparties)
        if len(sc.by_counterparty)
        else (0.0, pd.DataFrame())
    )
    ladder = cash_ladder(list(snap.trades), market, reporting, market.as_of)

    capital = pd.DataFrame(
        [
            {"component": "Market risk, FRTB SA", "capital": sa.total, "basis": "standardised"},
            {"component": "Market risk, FRTB IMA", "capital": ima.capital, "basis": "internal models"},
            {
                "component": "Counterparty credit risk, SA-CCR",
                "capital": sc.total_rwa * 0.08,
                "basis": "RWA x 8%",
            },
            {"component": "CVA risk, BA-CVA", "capital": cva_k, "basis": "reduced basic approach"},
        ]
    )
    # Desk attribution: FRTB (SA attributed) + SA-CCR (by EAD share of trades) + CVA (same share).
    keys = val[["trade_id", "desk_id", "business_id"]].drop_duplicates("trade_id")
    ead_share = pd.Series(dtype=float)
    if len(sc.trades):
        tr = sc.trades.merge(keys, on="trade_id", how="left")
        tr["abs_eff"] = tr["effective"].abs()
        ns_ead = sc.by_netting_set.set_index("netting_set_id")["ead"]
        tr["ead"] = (
            tr["abs_eff"]
            / tr.groupby("netting_set_id")["abs_eff"].transform("sum")
            * tr["netting_set_id"].map(ns_ead)
        )
        ead_share = tr.groupby("desk_id")["ead"].sum()
    by_desk = sa.by_desk.set_index("desk_id")[["attributed"]].rename(columns={"attributed": "frtb_sa"})
    if len(ima.by_desk):
        by_desk = by_desk.join(
            ima.by_desk.set_index("desk_id")[["attributed_capital"]].rename(
                columns={"attributed_capital": "frtb_ima"}
            ),
            how="outer",
        )
    tot_ead = ead_share.sum() if len(ead_share) else 0.0
    by_desk["saccr"] = (ead_share / tot_ead * sc.total_rwa * 0.08) if tot_ead else 0.0
    by_desk["ba_cva"] = (ead_share / tot_ead * cva_k) if tot_ead else 0.0
    by_desk = by_desk.fillna(0.0)
    by_desk["total_sa"] = by_desk["frtb_sa"] + by_desk["saccr"] + by_desk["ba_cva"]
    by_desk = (
        by_desk.reset_index()
        .merge(keys[["desk_id", "business_id"]].drop_duplicates("desk_id"), on="desk_id", how="left")
        .sort_values("total_sa", ascending=False)
    )

    out = RegulatoryRun(
        run_id,
        sa,
        ima,
        sc,
        im,
        cva_k,
        cva_detail,
        ladder,
        capital,
        by_desk,
        notes={"nmrf": sorted(set(nmrf)), "backtest_exceptions": exceptions},
    )
    if persist:
        repo.save_run_frame(run_id, "reg_capital", capital)
        repo.save_run_frame(run_id, "reg_by_desk", by_desk)
        repo.save_run_frame(run_id, "reg_frtb_sa_detail", sa.detail)
        repo.save_run_frame(run_id, "reg_frtb_sa_classes", pd.DataFrame([c.__dict__ for c in sa.classes]))
        repo.save_run_frame(run_id, "reg_frtb_ima_pla", ima.pla)
        repo.save_run_frame(run_id, "reg_frtb_ima_summary", pd.DataFrame([ima.summary()]))
        repo.save_run_frame(run_id, "reg_saccr_netting", sc.by_netting_set)
        repo.save_run_frame(run_id, "reg_saccr_counterparty", sc.by_counterparty)
        repo.save_run_frame(run_id, "reg_simm", im.by_netting_set)
        repo.save_run_frame(run_id, "reg_ba_cva", cva_detail)
        repo.save_run_frame(run_id, "reg_cash_ladder", ladder)
        repo.save_run_frame(run_id, "reg_summary", pd.DataFrame([out.summary()]))
    return out
