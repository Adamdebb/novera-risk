"""FRTB SA and IMA, SA-CCR, SIMM-lite, BA-CVA, cash ladder."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.counterparty_risk import ExposureSimConfig, run_counterparty
from novera.market_data.history import MarketHistory
from novera.regulatory import run_regulatory
from novera.regulatory.frtb_ima import liquidity_horizon, multiplier_from_exceptions
from novera.regulatory.frtb_sa import _aggregate, _girr_rho, frtb_sa
from novera.regulatory.saccr import maturity_factor
from novera.regulatory.simm import _ir_rho
from novera.risk import VaRConfig
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    build_limits,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.eod import EODConfig, run_eod

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("reg") / "reg.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=37))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=37, market_history=hist)
    )
    runs_dir = tmp_path_factory.mktemp("runs")
    with DuckDBRepository(path) as repo:
        repo.init_schema()
        repo.save_organisation(org)
        repo.save_counterparties(cp.counterparties)
        repo.save_netting_sets(cp.netting_sets, cp.csas)
        repo.save_limits(build_limits(org, cp))
        repo.save_portfolio_snapshot(gen.snapshot)
        repo.save_risk_factors(md.universe)
        repo.save_market_history(md.history)
        repo.save_market_snapshot(md.previous_snapshot)
        repo.save_market_snapshot(md.snapshot)
        res = run_eod(
            repo,
            EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=150), workers=1),
            runs_dir=runs_dir,
        )
    return {"path": str(path), "run_id": res.run.run_id, "runs_dir": runs_dir}


def test_frtb_building_blocks():
    assert _girr_rho("", "1.0", "1.0") == 1.0
    assert 0.4 <= _girr_rho("", "1.0", "30.0") < 1.0 and _girr_rho("", "5.0", "10.0") > _girr_rho(
        "", "1.0", "30.0"
    )
    # Two perfectly offsetting sensitivities in one bucket with rho=1 aggregate to zero.
    assert _aggregate({"b": {"x": 100.0, "y": -100.0}}, lambda b, a, c: 1.0, 0.5, 1.0) == pytest.approx(0.0)
    # Same sign, rho=0 -> sqrt(sum of squares).
    assert _aggregate({"b": {"x": 3.0, "y": 4.0}}, lambda b, a, c: 0.0, 0.5, 1.0) == pytest.approx(5.0)
    # Cross-bucket with gamma.
    v = _aggregate({"b1": {"x": 3.0}, "b2": {"y": 4.0}}, lambda b, a, c: 0.0, 0.5, 1.0)
    assert v == pytest.approx(np.sqrt(9 + 16 + 2 * 0.5 * 3 * 4))
    assert liquidity_horizon("IR:USD:10Y") == 10 and liquidity_horizon("IR:MXN:10Y") == 20
    assert liquidity_horizon("CDS:CDX.NA.HY") == 60 and liquidity_horizon("CRYPTO:BTC") == 120
    assert multiplier_from_exceptions(0) == 1.5 and multiplier_from_exceptions(7) == pytest.approx(1.83)
    assert multiplier_from_exceptions(12) == 2.0
    assert maturity_factor(0.5, False) == pytest.approx(np.sqrt(0.5)) and maturity_factor(5, False) == 1.0
    assert maturity_factor(5, True) == pytest.approx(1.5 * np.sqrt(10 / 250))
    assert _ir_rho("1Y", "1Y") == 1.0 and _ir_rho("1M", "30Y") == 0.4


def test_frtb_sa_scales_with_sensitivities():
    sens = pd.DataFrame(
        [
            {
                "trade_id": "a",
                "measure": "DV01",
                "factor_id": "IR:USD:10Y",
                "bucket": "10Y",
                "underlying": "USD",
                "bump": 1e-4,
                "value": -1000.0,
            },
            {
                "trade_id": "b",
                "measure": "EQ_DELTA",
                "factor_id": "EQIDX:SPX",
                "bucket": "",
                "underlying": "SPX",
                "bump": 0.01,
                "value": 5000.0,
            },
        ]
    )
    val = pd.DataFrame(
        [
            {
                "trade_id": "a",
                "desk_id": "D1",
                "status": "LIVE",
                "product_type": "INTEREST_RATE_SWAP",
                "pv": 0.0,
            },
            {
                "trade_id": "b",
                "desk_id": "D2",
                "status": "LIVE",
                "product_type": "EQUITY_INDEX_FUTURE",
                "pv": 0.0,
            },
        ]
    )
    r1 = frtb_sa(sens, val)
    r2 = frtb_sa(sens.assign(value=sens["value"] * 2), val)
    assert r2.total == pytest.approx(2 * r1.total)
    girr = next(c for c in r1.classes if c.risk_class == "GIRR")
    assert girr.delta == pytest.approx(1000 / 1e-4 * 0.011)  # one node, RW 1.1%
    eq = next(c for c in r1.classes if c.risk_class == "EQ")
    assert eq.delta == pytest.approx(5000 * 100 * 0.15)
    assert set(r1.by_desk["desk_id"]) == {"D1", "D2"} and r1.by_desk["attributed"].sum() == pytest.approx(
        r1.total
    )


def test_regulatory_run_end_to_end(db):
    with DuckDBRepository(db["path"]) as repo:
        rr = run_regulatory(repo, db["run_id"], runs_dir=db["runs_dir"])
        sm = rr.summary()
        assert sm["frtb_sa"] > 0 and sm["frtb_ima"] > 0 and sm["saccr_ead"] > 0 and sm["simm_im"] > 0
        assert sm["frtb_sa"] > sm["frtb_ima"], "standardised should exceed internal models on this book"
        assert (
            rr.frtb_ima.multiplier >= 1.5
            and rr.frtb_ima.imes >= max(rr.frtb_ima.es_by_horizon.values()) * 0.99
        )
        assert set(rr.frtb_ima.pla["zone"]) <= {"GREEN", "AMBER", "RED"}
        assert (rr.saccr.by_netting_set["multiplier"].between(0.05, 1.0)).all()
        assert (rr.saccr.by_counterparty["rwa"] <= rr.saccr.by_counterparty["ead"] * 1.5 + 1e-6).all()
        assert rr.ba_cva_capital > 0 and not rr.ba_cva_detail.empty
        assert not rr.cash_ladder.empty and (rr.cash_ladder["inflow"] >= 0).all()
        assert rr.by_desk["frtb_sa"].sum() == pytest.approx(rr.frtb_sa.total, rel=1e-6)
        assert not repo.load_run_frame(db["run_id"], "reg_summary").empty
        # Initial margin then reduces exposure in the counterparty engine.
        cr = run_counterparty(
            repo, db["run_id"], ExposureSimConfig(paths=30, seed=1), runs_dir=db["runs_dir"], workers=1
        )
        assert cr.notes["initial_margin_sets"] > 0
        prof = cr.profiles
        coll = prof[prof["collateralised"]]
        assert (coll["initial_margin"] > 0).any()


def test_eod_runs_regulatory_then_counterparty(db, tmp_path):
    with DuckDBRepository(db["path"]) as repo:
        res = run_eod(
            repo,
            EODConfig(
                counterparty=True,
                regulatory=True,
                exposure_paths=20,
                var=VaRConfig(window_days=150),
                workers=1,
            ),
            runs_dir=tmp_path,
        )
        assert "regulatory" in res.run.summary and "counterparty" in res.run.summary
        assert list(res.run.timings).index("regulatory") < list(res.run.timings).index("counterparty")
