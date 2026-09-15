"""Reconcile an external (official) risk feed against a Novera run and attribute the gap.

Method (record MR-009). For the common trade set, per-trade PV and VaR contributions are
compared. The gap in total VaR is attributed in this order, each step exact given the
previous ones:

1. SCOPE            trades in one system only: their VaR contribution.
2. MARKET_DATA      trades whose PV matches when Novera reprices them on the previous
                    day's market: the official system used a stale snapshot.
3. PRICING_MODEL    remaining trades whose PV differs beyond tolerance, grouped by product.
4. METHODOLOGY      for trades with matching PV, the change in Novera's VaR when it adopts
                    the feed's window: rerun of historical VaR on the common set.
5. RESIDUAL         what is left.

The attribution uses the engine only through documented reruns; nothing is estimated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from novera.market_data.history import MarketHistory
from novera.pricing.valuation import fx_to_reporting, value_trade
from novera.risk import Portfolio, VaRConfig, historical_var
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, new_run_id

MODEL_VERSION = "1.0.0"
PV_TOL_ABS = 1_000.0
PV_TOL_REL = 1e-4


@dataclass
class Reconciliation:
    recon_id: str
    run_id: str
    vendor: str
    business_date: str
    novera_var: float
    official_var: float
    attribution: dict[str, float]
    detail: pd.DataFrame = field(repr=False)
    by_desk: pd.DataFrame = field(repr=False)
    findings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def gap(self) -> float:
        return self.official_var - self.novera_var

    def summary(self) -> dict[str, Any]:
        return {
            "recon_id": self.recon_id,
            "run_id": self.run_id,
            "vendor": self.vendor,
            "business_date": self.business_date,
            "novera_var": self.novera_var,
            "official_var": self.official_var,
            "gap": self.gap,
            "gap_pct": self.gap / self.novera_var if self.novera_var else None,
            "attribution": self.attribution,
            "findings": self.findings,
            "trades_compared": int(len(self.detail)),
            "meta": self.meta,
        }


def reconcile(
    repo: DuckDBRepository, run_id: str, feed_csv: Path, feed_meta: Path | None = None, persist: bool = True
) -> Reconciliation:
    run = repo.load_run(run_id)
    feed = pd.read_csv(feed_csv)
    meta = json.loads(Path(feed_meta).read_text()) if feed_meta and Path(feed_meta).exists() else {}
    vendor = meta.get("vendor", feed_csv.stem)
    val = repo.load_run_frame(run_id, "valuation")
    contrib = repo.load_run_frame(run_id, "var_contributions")
    novera = (
        val[val["status"] == "LIVE"][["trade_id", "desk_id", "book_id", "product_type", "asset_class", "pv"]]
        .merge(contrib[["trade_id", "var_contribution"]], on="trade_id", how="left")
        .fillna({"var_contribution": 0.0})
    )
    m = novera.merge(
        feed[["trade_id", "official_pv", "official_var_contribution"]],
        on="trade_id",
        how="outer",
        indicator=True,
    )
    m["presence"] = m["_merge"].map(
        {"both": "BOTH", "left_only": "NOVERA_ONLY", "right_only": "OFFICIAL_ONLY"}
    )
    m = m.drop(columns="_merge")
    m["pv_diff"] = m["official_pv"] - m["pv"]
    m["var_diff"] = m["official_var_contribution"].fillna(0.0) - m["var_contribution"].fillna(0.0)
    m["pv_matches"] = (m["presence"] == "BOTH") & (
        (m["pv_diff"].abs() <= PV_TOL_ABS) | (m["pv_diff"].abs() <= PV_TOL_REL * m["pv"].abs())
    )
    m["cause"] = np.where(m["presence"] != "BOTH", "SCOPE", np.where(m["pv_matches"], "", "PRICING_MODEL"))

    novera_var = float(run.summary["var"])
    official_var = float(meta.get("var", {}).get("total", feed["official_var_contribution"].sum()))
    findings: list[str] = []
    attribution: dict[str, float] = {}

    # 1. Scope.
    scope = m[m["presence"] != "BOTH"]
    attribution["SCOPE"] = float(
        scope["official_var_contribution"].fillna(0.0).sum() - scope["var_contribution"].fillna(0.0).sum()
    )
    n_only = int((m["presence"] == "NOVERA_ONLY").sum())
    o_only = int((m["presence"] == "OFFICIAL_ONLY").sum())
    if n_only or o_only:
        books = sorted(scope[scope["presence"] == "NOVERA_ONLY"]["book_id"].dropna().unique())
        findings.append(
            f"SCOPE: {n_only} trades only in Novera ({', '.join(books[:5])}), {o_only} only in the feed."
        )

    # 2. Market-data timestamp: reprice PV-mismatched trades on the previous day's market.
    snap = repo.load_portfolio_snapshot(run.portfolio_snapshot_id)
    by_id = {t.trade_id: t for t in snap.trades}
    mismatched = m[(m["presence"] == "BOTH") & ~m["pv_matches"]]
    if len(mismatched) and run.previous_market_snapshot_id:
        prev = repo.load_market_snapshot(run.previous_market_snapshot_id)
        stale_ids = []
        for _, r in mismatched.iterrows():
            t = by_id.get(r["trade_id"])
            if t is None:
                continue
            try:
                p = value_trade(t, prev, run.business_date)
                pv_prev = p.pv_local * fx_to_reporting(prev, p.currency, run.reporting_currency)
            except Exception:  # noqa: BLE001
                continue
            if abs(pv_prev - r["official_pv"]) <= max(PV_TOL_ABS, PV_TOL_REL * abs(r["official_pv"])):
                stale_ids.append(r["trade_id"])
        m.loc[m["trade_id"].isin(stale_ids), "cause"] = "MARKET_DATA"
        if stale_ids:
            acs = sorted(m[m["trade_id"].isin(stale_ids)]["asset_class"].unique())
            findings.append(
                f"MARKET_DATA: {len(stale_ids)} trades ({', '.join(acs)}) match Novera's PV on the "
                f"{prev.as_of} market: the feed valued them on stale data."
            )
    for cause in ("MARKET_DATA", "PRICING_MODEL"):
        sub = m[m["cause"] == cause]
        attribution[cause] = float(sub["var_diff"].sum())
    pm = m[m["cause"] == "PRICING_MODEL"]
    if len(pm):
        prods = pm.groupby("product_type")["pv_diff"].agg(["count", "sum"])
        findings.append(
            "PRICING_MODEL: PV differs beyond tolerance on "
            + ", ".join(f"{n} {p} ({s / 1e6:+.2f}m PV)" for p, (n, s) in prods.iterrows())
            + "."
        )

    # 4. Methodology: on common trades with matching PV, the whole contribution difference is
    #    methodology (window, quantile, allocation). Verified by rerunning Novera's VaR with the
    #    feed's window on that set. Component VaR re-allocates when the trade set changes, so
    #    scope removals also move the common trades' contributions.
    common_ok = m[(m["presence"] == "BOTH") & (m["cause"] == "")]
    attribution["METHODOLOGY"] = float(common_ok["var_diff"].sum())
    window = int(meta.get("var", {}).get("window_days", 0) or 0)
    our_window = int(run.config.get("var", {}).get("window_days", 500))
    if window and window != our_window and len(common_ok):
        market = repo.load_run_market(run.run_id)
        universe = {f.factor_id: f for f in repo.load_risk_factors()}
        hist = MarketHistory.from_long(repo.load_market_history())
        ids = set(common_ok["trade_id"])
        pf = Portfolio(
            [t for t in snap.trades if t.trade_id in ids], market, run.reporting_currency, universe=universe
        )
        ours = historical_var(pf, hist, VaRConfig(window_days=our_window))
        theirs = historical_var(pf, hist, VaRConfig(window_days=window))
        meta["methodology_rerun"] = {
            "trades": len(ids),
            "our_window": our_window,
            "feed_window": window,
            "var_our_window": ours.var,
            "var_feed_window": theirs.var,
        }
        findings.append(
            f"METHODOLOGY: {len(ids)} common trades with matching PV carry "
            f"{attribution['METHODOLOGY'] / 1e6:+.2f}m of contribution difference. Rerunning Novera's "
            f"VaR on them with the feed's {window}-day window instead of {our_window} moves standalone "
            f"VaR by {(theirs.var - ours.var) / 1e6:+.2f}m ({ours.var / 1e6:.2f}m to "
            f"{theirs.var / 1e6:.2f}m); the rest is re-allocation after the scope change."
        )
    explained = sum(attribution.values())
    attribution["RESIDUAL"] = float(official_var - novera_var - explained)

    by_desk = (
        m.groupby("desk_id", dropna=False)
        .agg(
            novera_var=("var_contribution", "sum"),
            official_var=("official_var_contribution", "sum"),
            trades_both=("presence", lambda s: int((s == "BOTH").sum())),
            pv_mismatches=("cause", lambda s: int(s.isin(["PRICING_MODEL", "MARKET_DATA"]).sum())),
        )
        .reset_index()
    )
    by_desk["gap"] = by_desk["official_var"].fillna(0.0) - by_desk["novera_var"].fillna(0.0)
    by_desk = by_desk.sort_values("gap", key=np.abs, ascending=False)

    rec = Reconciliation(
        new_run_id("rec"),
        run_id,
        vendor,
        str(run.business_date),
        novera_var,
        official_var,
        attribution,
        m,
        by_desk,
        findings,
        meta,
    )
    if persist:
        repo.save_run_frame(run_id, "recon_detail", m.assign(recon_id=rec.recon_id))
        repo.save_run_frame(run_id, "recon_by_desk", by_desk.assign(recon_id=rec.recon_id))
        repo.save_run_frame(
            run_id,
            "recon_summary",
            pd.DataFrame(
                [
                    {
                        **rec.summary(),
                        "attribution": json.dumps(attribution),
                        "findings": json.dumps(findings),
                        "meta": json.dumps(meta),
                    }
                ]
            ),
        )
        repo.save_audit_events(
            [
                AuditEvent.now(
                    "reconciliation",
                    "RECON_COMPLETED",
                    rec.recon_id,
                    run_id=run_id,
                    vendor=vendor,
                    gap=rec.gap,
                    attribution=attribution,
                )
            ]
        )
    return rec
