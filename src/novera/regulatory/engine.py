"""Regulatory capital run for a stored market-risk run: FRTB SA and IMA, SA-CCR, SIMM-lite
initial margin, BA-CVA, funding cash ladder, and capital attributed to desk and business."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from novera.config import Settings, get_settings
from novera.counterparty_risk.ccp import build_ccp_sets, qccp_capital
from novera.market_data.history import MarketHistory
from novera.pricing.collateral import CollateralMap
from novera.pricing.valuation import fx_to_reporting
from novera.regulatory.allocation import (
    COMPONENTS,
    DeskAllocation,
    assemble,
    ba_cva_by_desk,
    ccp_by_desk,
    desk_map,
    saccr_by_set,
    saccr_capital,
)
from novera.regulatory.cash_ladder import cash_ladder
from novera.regulatory.cva_capital import ba_cva
from novera.regulatory.frtb_ima import IMAResult, frtb_ima
from novera.regulatory.frtb_sa import FRTBSAResult, frtb_sa
from novera.regulatory.saccr import SACCRResult, saccr
from novera.regulatory.simm import SIMMResult, simm
from novera.risk.revaluation import Portfolio
from novera.storage.duckdb_repository import DuckDBRepository

MODEL_VERSION = "1.1.0"
COMPONENT_KEYS = {label: key for key, label in COMPONENTS.items()}  # capital table label -> key


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
    ccp: pd.DataFrame = field(default_factory=pd.DataFrame)  # qualifying-CCP capital per CCP
    allocation: DeskAllocation | None = None  # capital by desk: Euler, incremental, standalone

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
            "simm_im_posted": float(self.simm.by_netting_set["im_posted"].sum())
            if len(self.simm.by_netting_set)
            else 0.0,
            "ccp_capital": float(self.ccp["capital"].sum()) if len(self.ccp) else 0.0,
            "ba_cva_capital": self.ba_cva_capital,
            "pla_red_desks": int((self.frtb_ima.pla["zone"] == "RED").sum()) if len(self.frtb_ima.pla) else 0,
        }


def exotic_gross_notional(trades, market, reporting: str) -> pd.Series:
    """Gross notional (contracts × multiplier × strike, in reporting currency) of barrier and
    digital options, the products that attract the residual risk add-on (REG-001)."""
    out: dict[str, float] = {}
    for t in trades:
        if t.product_type.value != "EQUITY_EXOTIC" or t.status.value != "LIVE":
            continue
        ins = t.instrument
        fx = fx_to_reporting(market, t.currency, reporting)
        out[t.trade_id] = abs(t.quantity * ins.contract_multiplier * ins.strike * fx)
    return pd.Series(out, dtype=float)


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
    market = repo.load_run_market(run.run_id)
    universe = {f.factor_id: f for f in repo.load_risk_factors()}
    history = MarketHistory.from_long(repo.load_market_history())
    val = repo.load_run_frame(run_id, "valuation")
    sens = repo.load_run_frame(run_id, "sensitivities")
    netting_sets, csas = repo.load_netting_sets()
    counterparties = {c.counterparty_id: c for c in repo.load_counterparties()}
    reporting = run.reporting_currency
    ccp = build_ccp_sets(snap.trades, repo.load_organisation(run.config.get("firm_id", "GMB")))
    base_vols = {
        u: market.vol_surface(u).atm(0.25)
        for u in {f.split(":")[1] for f in market.factors_with_prefix("VOL:")}
    }
    for ccy in {f.split(":")[1] for f in market.factors_with_prefix("SWVOL:")}:
        base_vols[f"SWVOL:{ccy}"] = float(
            np.mean([market.values[f] for f in market.factors_with_prefix(f"SWVOL:{ccy}:")])
        )
    exotic_notional = exotic_gross_notional(snap.trades, market, reporting)
    # Valuation lacks netting_set_id; add it from the snapshot for SIMM.
    ns_by_trade = {t.trade_id: t.netting_set_id for t in snap.trades} | ccp.by_trade
    val = val.assign(netting_set_id=val["trade_id"].map(ns_by_trade))

    sa = frtb_sa(sens, val, base_vols, exotic_notional)
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
    pf = Portfolio(
        snap.trades,
        market,
        reporting,
        universe=universe,
        collateral=CollateralMap.from_reference(netting_sets, csas),
    )
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
    im = simm(
        sens,
        val,
        base_vols,
        csa_sets={n.netting_set_id for n in netting_sets if n.csa_id},
        threshold=settings.im_threshold,
        ccp_sets=ccp.ids,
    )
    sc = saccr(list(snap.trades), market, netting_sets, csas, counterparties, reporting, market.as_of)
    # Exposure to qualifying CCPs (decision 39.4): margined SA-CCR on the CCP sets plus the margin
    # we post there, at 2%, and the default-fund charge.
    sc_ccp = saccr(
        ccp.trades,
        market,
        ccp.netting_sets,
        ccp.csas,
        counterparties,
        reporting,
        market.as_of,
        cleared=True,
    )
    ccp_ead = (
        sc_ccp.by_netting_set.groupby("counterparty_id")["ead"].sum().to_dict()
        if len(sc_ccp.by_netting_set)
        else {}
    )
    posted = im.by_netting_set[im.by_netting_set["ccp"]] if len(im.by_netting_set) else im.by_netting_set
    ccp_cap = qccp_capital(
        ccp_ead, posted.groupby("counterparty_id")["im_posted"].sum().to_dict() if len(posted) else {}
    )
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
            {
                "component": "CCP trade exposure",
                "capital": float(ccp_cap["trade_exposure_capital"].sum()) if len(ccp_cap) else 0.0,
                "basis": "2% risk weight, qualifying CCP",
            },
            {
                "component": "CCP default fund",
                "capital": float(ccp_cap["default_fund_capital"].sum()) if len(ccp_cap) else 0.0,
                "basis": "K_CM, CRE54 simplified",
            },
        ]
    )
    # Desk allocation (REG-008): Euler contributions are the official figures; incremental and
    # standalone charges sit beside them.
    desk_of = desk_map(val)
    sets = saccr_by_set(sc, desk_of)
    trade_ccp, fund_ccp = ccp_by_desk(sc_ccp, ccp_cap, sens, val, base_vols, set(ccp.ids), desk_of)
    keys = val[["desk_id", "business_id"]].dropna().drop_duplicates("desk_id")
    alloc = assemble(
        {
            "frtb_sa": sa.by_desk[["desk_id", "euler_raw", "incremental", "standalone"]],
            "frtb_ima": ima.by_desk[["desk_id", "euler_raw", "incremental", "standalone"]],
            "saccr": saccr_capital(sets, sc),
            "ba_cva": ba_cva_by_desk(cva_k, cva_detail, sets),
            "ccp_trade": trade_ccp,
            "ccp_default_fund": fund_ccp,
        },
        dict(zip(capital["component"].map(COMPONENT_KEYS), capital["capital"], strict=True)),
        dict(zip(keys["desk_id"], keys["business_id"], strict=True)),
    )
    by_desk = alloc.by_desk

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
        notes={
            "nmrf": sorted(set(nmrf)),
            "backtest_exceptions": exceptions,
            "im_threshold": settings.im_threshold,
        },
        ccp=ccp_cap,
        allocation=alloc,
    )
    if persist:
        repo.save_run_frame(run_id, "reg_capital", capital)
        repo.save_run_frame(run_id, "reg_by_desk", by_desk)
        repo.save_run_frame(run_id, "reg_desk_allocation", alloc.rows)
        repo.save_run_frame(run_id, "reg_allocation_summary", alloc.summary)
        repo.save_run_frame(run_id, "reg_frtb_sa_detail", sa.detail)
        repo.save_run_frame(run_id, "reg_frtb_sa_classes", pd.DataFrame([c.__dict__ for c in sa.classes]))
        repo.save_run_frame(run_id, "reg_frtb_ima_pla", ima.pla)
        repo.save_run_frame(run_id, "reg_frtb_ima_summary", pd.DataFrame([ima.summary()]))
        repo.save_run_frame(run_id, "reg_saccr_netting", sc.by_netting_set)
        repo.save_run_frame(run_id, "reg_saccr_counterparty", sc.by_counterparty)
        repo.save_run_frame(run_id, "reg_simm", im.by_netting_set)
        repo.save_run_frame(run_id, "reg_ccp", ccp_cap)
        repo.save_run_frame(run_id, "reg_ba_cva", cva_detail)
        repo.save_run_frame(run_id, "reg_cash_ladder", ladder)
        repo.save_run_frame(run_id, "reg_summary", pd.DataFrame([out.summary()]))
    return out
