"""Portfolio Lab runner (LAB-001).

A lab is a self-contained sandbox database under ``data/lab/<name>.duckdb``: the chosen
template (bank or fund) is simulated with only the selected planted problems, each scaled by
one multiplier, then the governed EOD runs on it exactly as in production. Detection is
read back from the stored run frames with the same rules a risk manager would apply: which
limits went to WARNING or BREACH, which data-quality findings touched the planted trades,
which flags name them. The lab never changes the production databases.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from novera.config import Settings, get_settings
from novera.simulation.market_data import MARKET_PROBLEMS
from novera.simulation.trades import BANK_PROBLEMS, FUND_PROBLEMS, Injection
from novera.storage.duckdb_repository import DuckDBRepository

MODEL_VERSION = "1.0.0"

# What each problem is expected to trip, and how the lab looks for it in the stored run.
PROBLEM_CATALOGUE: dict[str, dict[str, Any]] = {
    "usd_10y_concentration": {
        "template": "bank",
        "title": "USD 10Y DV01 concentration facing Bank A",
        "limits": ["USD_RATES_DV01_USD_10Y", "USD_RATES_CONC_10Y", "USD_RATES_DV01_USD", "CPTY_BANK_A"],
        "flags": ["10Y", "USD"],
    },
    "illiquid_brent": {
        "template": "bank",
        "title": "Illiquid far-dated Brent position",
        "limits": ["ENERGY_CONC_BRENT", "ENERGY_CMD_DELTA"],
        "flags": ["BRENT"],
        "liquidity": True,
    },
    "btc_exposure": {
        "template": "bank",
        "title": "Outsized BTC exposure",
        "limits": ["DIGITAL_STRESS", "DIGITAL_BTC_DELTA", "DIGITAL_VAR", "DIGITAL_ASSETS_VAR"],
        "flags": ["BTC"],
    },
    "wrong_way_sovereign": {
        "template": "bank",
        "title": "Wrong-way USD/ARS forwards with an EM sovereign",
        "limits": ["CPTY_SOV_EM"],
        "wrong_way": "SOV_EM",
    },
    "wrong_way_credit": {
        "template": "bank",
        "title": "HY protection bought from a HY-rated corporate",
        "limits": ["CPTY_CORP_AIR"],
        "wrong_way": "CORP_AIR",
    },
    "invalid_trades": {
        "template": "bank",
        "title": "Trades with invalid booking data",
        "dq_codes": [
            "TRADE_INVALID",
            "TRADE_UNKNOWN_COUNTERPARTY",
            "TRADE_UNKNOWN_NETTING_SET",
            "TRADE_UNKNOWN_BOOK",
        ],
    },
    "stale_eurusd_vol_surface": {
        "template": "market",
        "title": "Stale EUR/USD vol surface",
        "dq_codes": ["MD_STALE_FACTOR"],
        "dq_subject": "VOL:EURUSD:",
    },
    "missing_usd_7y_node": {
        "template": "market",
        "title": "Missing USD 7Y curve node",
        "dq_codes": ["MD_MISSING_FACTOR"],
        "dq_subject": "IR:USD:7Y",
    },
    "crowded_single_name": {
        "template": "hedge_fund",
        "title": "Crowded NVDA long at 15% of NAV",
        "limits": ["EQ_LS_US_CONC_NVDA", "EQ_LS_US_VAR", "EQ_LS_US_EQ_DELTA"],
        "flags": ["NVDA"],
    },
    "pb_concentration": {
        "template": "hedge_fund",
        "title": "Prime-broker concentration in PB_GS",
        "limits": ["FUND_PB_CONCENTRATION", "CPTY_PB_GS", "FUND_MARGIN_USAGE"],
        "flags": ["PB_GS"],
    },
    "illiquid_vs_redemptions": {
        "template": "hedge_fund",
        "title": "Far-dated natural gas against monthly dealing",
        "limits": ["COMMODITY_TREND_STRESS"],
        "flags": ["NATGAS"],
        "liquidity": True,
    },
    "short_vol": {
        "template": "hedge_fund",
        "title": "Large short index-vol book",
        "limits": ["INDEX_VOL_ARB_VEGA", "INDEX_VOL_ARB_STRESS", "INDEX_VOL_ARB_VAR", "FUND_STRESS"],
        "flags": ["SPX", "NDX", "SX5E"],
    },
}


@dataclass(frozen=True)
class LabSpec:
    name: str
    template: str = "bank"  # bank | hedge_fund
    problems: tuple[str, ...] = ()
    market_problems: tuple[str, ...] = ()
    scale: float = 1.0
    n_trades: int = 600
    seed: int = 42
    business_date: date = date(2026, 9, 11)
    years: float = 2.0
    counterparty: bool = False
    regulatory: bool = False
    workers: int | None = None

    def validate(self) -> None:
        if self.template not in ("bank", "hedge_fund"):
            raise ValueError("template must be bank or hedge_fund")
        allowed = BANK_PROBLEMS if self.template == "bank" else FUND_PROBLEMS
        bad = [p for p in self.problems if p not in allowed]
        if bad:
            raise ValueError(f"unknown problems for {self.template}: {bad}; choose from {list(allowed)}")
        bad = [p for p in self.market_problems if p not in MARKET_PROBLEMS]
        if bad:
            raise ValueError(f"unknown market problems: {bad}; choose from {list(MARKET_PROBLEMS)}")
        if not (0.05 <= self.scale <= 20):
            raise ValueError("scale must be between 0.05 and 20")
        if not self.name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("name must be alphanumeric with - or _")

    def db_path(self, settings: Settings | None = None) -> Path:
        s = settings or get_settings()
        return Path(s.data_dir) / "lab" / f"{self.name}.duckdb"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["business_date"] = self.business_date.isoformat()
        return d


@dataclass
class Detection:
    problem: str
    title: str
    description: str
    expected: str
    detected: bool
    evidence: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)  # limits that stayed inside, with utilisation
    needs: str = ""  # what would have been needed (e.g. counterparty engine)


@dataclass
class LabResult:
    spec: LabSpec
    run_id: str
    db_path: str
    summary: dict[str, Any]
    detections: list[Detection]
    seconds: float

    @property
    def detected(self) -> int:
        return sum(1 for d in self.detections if d.detected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "run_id": self.run_id,
            "db_path": self.db_path,
            "summary": self.summary,
            "detections": [asdict(d) for d in self.detections],
            "detected": self.detected,
            "planted": len(self.detections),
            "seconds": round(self.seconds, 1),
        }


def run_lab(spec: LabSpec, settings: Settings | None = None) -> LabResult:
    from novera.market_data.history import MarketHistory
    from novera.simulation import (
        BANK_TEMPLATE,
        FUND_TEMPLATE,
        TradeGeneratorConfig,
        build_counterparty_universe,
        build_fund,
        build_fund_counterparties,
        build_fund_limits,
        build_global_macro_bank,
        build_limits,
        build_multi_strategy_fund,
        generate_portfolio,
    )
    from novera.simulation.market_data import MarketSimConfig, generate_market_data
    from novera.workflows.eod import EODConfig, run_eod

    spec.validate()
    settings = settings or get_settings()
    t0 = time.perf_counter()
    fund = None
    if spec.template == "hedge_fund":
        org = build_multi_strategy_fund()
        cp = build_fund_counterparties(org)
        fund = build_fund()
        tmpl = FUND_TEMPLATE
    else:
        org = build_global_macro_bank()
        cp = build_counterparty_universe(org)
        tmpl = BANK_TEMPLATE
    md = generate_market_data(
        MarketSimConfig(
            end_date=spec.business_date,
            years=spec.years,
            seed=spec.seed,
            plant_data_quality_problems=bool(spec.market_problems),
            market_problems=spec.market_problems or None,
            problem_date=spec.business_date,
            snapshot_days=2,
        )
    )
    history = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org,
        cp,
        TradeGeneratorConfig(
            spec.business_date,
            spec.n_trades,
            spec.seed,
            bool(spec.problems),
            history,
            tmpl,
            problems=spec.problems or None,
            problem_scale=spec.scale,
        ),
    )
    db = spec.db_path(settings)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    with DuckDBRepository(db) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        if fund is not None:
            repo.save_fund(fund)
            repo.save_limits(build_fund_limits(org, cp, fund))
        else:
            repo.save_limits(build_limits(org, cp, spec.business_date))
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        for snap in md.snapshots.values():
            repo.save_market_snapshot(snap)
        repo.save_lab_spec(spec.name, spec.to_dict(), [i.model_dump() for i in gen.injections], md.planted)
        res = run_eod(
            repo,
            EODConfig(
                firm_id=org.firm.firm_id,
                counterparty=spec.counterparty,
                regulatory=spec.regulatory,
                workers=spec.workers,
                actor="portfolio-lab",
                exposure_paths=200 if spec.counterparty else None,
            ),
            business_date=spec.business_date,
            runs_dir=db.parent / "runs",
        )
        detections = detect(repo, res.run.run_id, gen.injections, md.planted, spec)
        summary = {
            "business_date": str(res.run.business_date),
            "verdict": res.run.verdict,
            "var": res.run.summary.get("var"),
            "breaches": res.run.summary.get("breaches"),
            "warnings": res.run.summary.get("warnings"),
            "dq_findings": res.run.summary.get("dq_findings"),
            "trades": len(gen.snapshot.trades),
        }
        repo.save_lab_result(spec.name, res.run.run_id, [asdict(d) for d in detections], summary)
    return LabResult(spec, res.run.run_id, str(db), summary, detections, time.perf_counter() - t0)


def detect(
    repo: DuckDBRepository,
    run_id: str,
    injections: list[Injection],
    planted_market: list[str],
    spec: LabSpec,
) -> list[Detection]:
    """Read the stored run and decide, per planted problem, whether the platform caught it."""
    limits = repo.load_run_frame(run_id, "limits")
    dq = repo.load_run_frame(run_id, "dq_findings")
    flags = repo.load_run_frame(run_id, "risk_flags")
    try:
        liq = repo.load_run_frame(run_id, "liquidity_trades")
    except Exception:  # noqa: BLE001
        liq = pd.DataFrame()
    wwr = pd.DataFrame()
    if spec.counterparty:
        try:
            wwr = repo.load_run_frame(run_id, "cp_wwr")
        except Exception:  # noqa: BLE001
            wwr = pd.DataFrame()
    out: list[Detection] = []
    items: list[tuple[str, str, tuple[str, ...], str]] = [
        (i.name, i.description, i.trade_ids, i.expected_detection) for i in injections
    ]
    for line in planted_market:
        name, _, desc = line.partition(": ")
        items.append((name, desc, (), "Data-quality finding on the market snapshot."))
    for name, description, trade_ids, expected in items:
        cat = PROBLEM_CATALOGUE.get(name, {})
        ev: list[str] = []
        context: list[str] = []
        needs = ""
        for lid in cat.get("limits", []):
            row = limits[limits["limit_id"] == lid] if len(limits) else pd.DataFrame()
            if not len(row):
                continue
            util = float(row.iloc[0]["utilisation"])
            if row.iloc[0]["status"] in ("BREACH", "WARNING"):
                ev.append(f"limit {lid} {row.iloc[0]['status']} at {util:.0%}")
            else:
                context.append(f"limit {lid} inside at {util:.0%}")
        if len(dq):
            for _, r in dq.iterrows():
                code = str(r["code"])
                affected = set(str(r.get("affected_trade_ids", "") or "").split(","))
                if trade_ids and code.startswith("TRADE_") and affected & set(trade_ids):
                    ev.append(f"data-quality finding {code} on {r['subject']}")
                elif code in cat.get("dq_codes", []) and str(r["subject"]).startswith(
                    cat.get("dq_subject", str(r["subject"]))
                ):
                    ev.append(f"data-quality finding {code} on {r['subject']}")
        if len(flags):
            for _, r in flags.iterrows():
                msg = str(r["message"])
                if any(tid in msg for tid in trade_ids) or any(k in msg for k in cat.get("flags", [])):
                    ev.append(f"{r['kind']} flag: {msg[:120]}")
        if cat.get("liquidity") and len(liq) and trade_ids:
            slow = liq[liq["trade_id"].isin(trade_ids) & (liq["days_to_liquidate"] > 10)]
            for _, r in slow.iterrows():
                ev.append(f"{r['trade_id']} needs {r['days_to_liquidate']:.0f} days to liquidate")
        if cat.get("wrong_way"):
            if not spec.counterparty:
                needs = "counterparty engine (enable counterparty exposure in the lab spec)"
            elif len(wwr):
                hit = wwr[(wwr["counterparty_id"] == cat["wrong_way"]) & wwr["wrong_way"].astype(bool)]
                for _, r in hit.iterrows():
                    ev.append(f"wrong-way flag on {r['counterparty_id']} against {r['proxy']}")
        out.append(
            Detection(
                name,
                cat.get("title", name),
                description,
                expected,
                bool(ev),
                sorted(set(ev)),
                context,
                needs,
            )
        )
    return out


def list_labs(settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    root = Path(s.data_dir) / "lab"
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for p in sorted(root.glob("*.duckdb")):
        try:
            with DuckDBRepository(p, read_only=True) as repo:
                rec = repo.load_lab(p.stem)
        except Exception:  # noqa: BLE001
            rec = None
        out.append({"name": p.stem, "db_path": str(p), **(rec or {})})
    return out
