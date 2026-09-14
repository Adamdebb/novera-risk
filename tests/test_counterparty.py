"""Counterparty risk: path simulation, collateral, exposure metrics, CVA, wrong-way, what-if."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.counterparty_risk import ExposureSimConfig, csa_what_if, load_exposure_result, run_counterparty
from novera.counterparty_risk.cva import CreditTerms, cva_from_profile, hazard_from_pd, hazard_from_spread
from novera.counterparty_risk.exposure import collateral_balance
from novera.counterparty_risk.simulation import FactorPaths, grid_dates
from novera.domain.counterparties import CSA
from novera.market_data.history import MarketHistory
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
SMALL = ExposureSimConfig(paths=40, seed=3)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("cp") / "cp.duckdb"
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.0, seed=29))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=29,
                                                           market_history=hist))
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
        res = run_eod(repo, EODConfig(counterparty=False, regulatory=False, var=VaRConfig(window_days=150), workers=1),
                      runs_dir=runs_dir)
    return {"path": str(path), "run_id": res.run.run_id, "runs_dir": runs_dir, "md": md, "gen": gen}


def test_grid_and_paths_are_seeded_and_bounded(db):
    md = db["md"]
    grid = grid_dates(AS_OF)
    assert [g[0] for g in grid][:3] == ["1W", "2W", "1M"] and grid[-1][2] > 14.9
    hist = MarketHistory.from_long(md.history)
    universe = {f.factor_id: f for f in md.universe}
    cfg = ExposureSimConfig(paths=20, seed=5, window_days=150, grid=grid_dates.__defaults__[0][:4])
    a = list(FactorPaths(md.snapshot, hist, universe, cfg))
    b = list(FactorPaths(md.snapshot, hist, universe, cfg))
    assert len(a) == 4 and len(a[0][3]) == 20
    assert a[2][3][7].values == b[2][3][7].values
    for _, _, _, snaps in a:
        for s in snaps[:5]:
            assert all(v >= -0.01 for k, v in s.values.items() if k.startswith("IR:"))
            assert all(v >= 0.03 for k, v in s.values.items() if k.startswith("VOL:"))
            assert all(v > 0 for k, v in s.values.items() if k.startswith(("EQ", "FX", "CMD", "CRYPTO")))


def test_collateral_balance_rules():
    years = np.array([0.02, 0.04, 0.25, 1.0])
    v = np.array([[10e6, 10e6], [12e6, -12e6], [30e6, -30e6], [30e6, -30e6]])
    csa = CSA(csa_id="x", collateral_currency="USD", threshold_they_post=5e6, threshold_we_post=0.0,
              minimum_transfer_amount=1e6, independent_amount=0.0, rounding=0.0, haircut=0.0)
    bal = collateral_balance(v, years, csa, 10)
    assert bal.shape == v.shape
    # Long horizon, positive value: they post value minus threshold (lag negligible at 1y).
    assert bal[3, 0] == pytest.approx(25e6, rel=0.02)
    # Negative value: we post the full amount (zero threshold on our side).
    assert bal[3, 1] == pytest.approx(-30e6, rel=0.02)
    # Below MTA nothing moves.
    small = np.array([[5.5e6]] * 4)
    assert (collateral_balance(small, years, csa, 10) == 0).all()
    assert (collateral_balance(v, years, None, 10) == 0).all()
    # Haircut and independent amount.
    csa2 = csa.model_copy(update={"haircut": 0.1, "independent_amount": 2e6})
    assert collateral_balance(v, years, csa2, 10)[3, 0] == pytest.approx(25e6 * 0.9 + 2e6, rel=0.02)


def test_cva_formula_and_hazards():
    assert hazard_from_pd(0.01) == pytest.approx(-np.log(0.99))
    assert hazard_from_spread(120, 0.4) == pytest.approx(0.02)
    years = np.array([0.5, 1.0, 2.0])
    ee = np.array([10e6, 10e6, 10e6])
    df = np.ones(3)
    cva = cva_from_profile(years, ee, df, CreditTerms(hazard_from_pd(0.02), 0.6))
    assert cva == pytest.approx(0.6 * 10e6 * (1 - np.exp(-hazard_from_pd(0.02) * 2.0)))
    assert cva_from_profile(years, ee * 0, df, CreditTerms(0.1, 0.6)) == 0.0


def test_run_counterparty_end_to_end(db):
    with DuckDBRepository(db["path"]) as repo:
        cr = run_counterparty(repo, db["run_id"], SMALL, runs_dir=db["runs_dir"], workers=1)
        prof, cps, cva, wwr = cr.profiles, cr.counterparty_summary, cr.cva, cr.wwr
        assert set(prof["step"]) == {g[0] for g in cr.result.grid}
        assert (prof["pfe99"] >= prof["pfe95"] - 1e-6).all()
        assert (prof["pfe95"] >= prof["ee"] - 1e-6).mean() > 0.9  # small-sample skew can invert a few rows
        # Where the counterparty owes us on average, collateral reduces expected exposure.
        owed = prof[prof["collateralised"] & (prof["mean_value"] > 0) & (prof["mean_collateral"] > 0)]
        assert len(owed) and (owed["ee"] <= owed["ee_gross"] + 1e-6).mean() > 0.9
        uncoll = prof[~prof["collateralised"]]
        assert np.allclose(uncoll["ee"], uncoll["ee_gross"]) and set(uncoll["counterparty_id"]) <= {
            "CORP_ENERGY", "CORP_AIR", "SOV_EM"}
        assert (cva["cva"] >= 0).all() and (cva["dva"] >= 0).all()
        assert (cva["cva"] <= cva["cva_gross"] + 1e-6).mean() > 0.8  # our posted collateral can raise it
        assert {"BANK_A", "SOV_EM", "CORP_AIR"} <= set(cps["counterparty_id"])
        assert wwr[wwr["counterparty_id"] == "CORP_AIR"]["proxy"].iloc[0] == "CDS:CDX.NA.HY"
        assert wwr[wwr["counterparty_id"] == "SOV_EM"]["proxy"].iloc[0] == "FX:USDARS"
        assert not cr.stressed_ce.empty and (cr.stressed_ce["stressed_exposure"] >= 0).all()
        # Stored and reloadable for what-ifs.
        assert not repo.load_run_frame(db["run_id"], "cp_counterparty_summary").empty
        res = load_exposure_result(repo, db["run_id"], db["runs_dir"])
        assert res is not None and res.paths == SMALL.paths
        ns = next(k for k in res.netting_values if res.netting_sets[k].counterparty_id == "BANK_A")
        base_csa = res.csas[res.netting_sets[ns].csa_id]
        loose = base_csa.model_copy(update={"threshold_they_post": 1e9})
        tight = csa_what_if(res, ns, base_csa)
        none = csa_what_if(res, ns, None)
        looser = csa_what_if(res, ns, loose)
        assert tight["pfe95"].max() <= none["pfe95"].max() + 1e-6
        assert looser["pfe95"].max() == pytest.approx(none["pfe95"].max())


def test_eod_with_counterparty_updates_limits(db, tmp_path):
    with DuckDBRepository(db["path"]) as repo:
        res = run_eod(repo, EODConfig(counterparty=True, exposure_paths=30, var=VaRConfig(window_days=150),
                                      workers=1), runs_dir=tmp_path)
        assert "counterparty" in res.run.summary and res.run.summary["counterparty"]["counterparties"] > 5
        lt = repo.load_run_frame(res.run.run_id, "limits")
        cp_rows = lt[lt["limit_type"] == "COUNTERPARTY_EXPOSURE"].set_index("entity_id")
        cps = repo.load_run_frame(res.run.run_id, "cp_counterparty_summary").set_index("counterparty_id")
        common = cp_rows.index.intersection(cps.index)
        assert len(common) > 5
        assert np.allclose(cp_rows.loc[common, "current"], cps.loc[common, "peak_pfe95"])
        assert "counterparty" in res.run.timings
        assert isinstance(pd.DataFrame(), pd.DataFrame)
