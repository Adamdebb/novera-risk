"""Concentration and liquidation stress (MR-025, round 53)."""

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from novera.fund.engine import delta_equivalent
from novera.risk import liquidation_stress as ls
from tests.test_stress_paths import _usd_rates_result, build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


def _trade(product, **ins):
    return SimpleNamespace(
        product_type=SimpleNamespace(value=product),
        instrument=SimpleNamespace(**ins),
        currency=ins.pop("ccy", "USD") if "ccy" in ins else "USD",
        trade_id=ins.get("tid", "T1"),
    )


def test_markets_pool_what_shares_one_volume():
    a = _trade("CASH_EQUITY", ticker="NVDA")
    assert ls.market_of(a) == ("CASH_EQUITY:NVDA", "NAME", "NVDA")
    f1 = _trade("COMMODITY_FUTURE", commodity="BRENT", expiry_date=date(2027, 8, 20))
    f2 = _trade("COMMODITY_FUTURE", commodity="BRENT", expiry_date=date(2027, 8, 31))
    f3 = _trade("COMMODITY_FUTURE", commodity="BRENT", expiry_date=date(2027, 9, 20))
    assert ls.market_of(f1)[0] == ls.market_of(f2)[0] != ls.market_of(f3)[0]
    assert ls.market_of(f1)[1] == "DELIVERY"
    o1 = _trade("EQUITY_OPTION", underlying="NVDA", strike=100.0, expiry_date=date(2026, 12, 18))
    o2 = _trade("EQUITY_OPTION", underlying="NVDA", strike=101.0, expiry_date=date(2026, 12, 18))
    o3 = _trade("EQUITY_OPTION", underlying="NVDA", strike=120.0, expiry_date=date(2026, 12, 18))
    assert ls.market_of(o1)[0] == ls.market_of(o2)[0] != ls.market_of(o3)[0]
    assert ls.market_of(o1)[1:] == ("STRIKE", "NVDA")
    s1 = SimpleNamespace(
        product_type=SimpleNamespace(value="INTEREST_RATE_SWAP"),
        instrument=SimpleNamespace(),
        currency="USD",
        trade_id="IRS_1",
    )
    s2 = SimpleNamespace(**{**s1.__dict__, "trade_id": "IRS_2"})
    assert ls.market_of(s1)[0] != ls.market_of(s2)[0] and ls.market_of(s1)[1] == "OTC"
    assert ls.sector_of("NVDA").startswith("EQ:") and ls.sector_of("nobody") == ""


def test_daily_vol_is_the_full_stressed_covariance(world):
    space = world["space"]
    rng = np.random.default_rng(3)
    e = rng.normal(size=(3, len(space.factors)))
    corr = (space.components * space.eigenvalues) @ space.components.T
    cov = corr * np.outer(space.sigma, space.sigma) / space.horizon
    direct = np.sqrt(np.einsum("ij,jk,ik->i", e, cov, e))
    assert np.allclose(ls.daily_vol(space, e), direct, rtol=1e-8)


def _run(world, liq=None, results=None):
    pf, val = world["pf"], world["val"]
    exposures, _ = delta_equivalent(val, world["sens"], pf.by_id)
    return ls.run_liquidation_stress(
        pf,
        world["book"],
        world["space"],
        world["hist"],
        val,
        liq if liq is not None else world["liq"],
        exposures,
        results or [],
        set(),
        0.2,
        0.5,
        2.0,
        1.0,
    )


def _with_pnl(world, r):
    r.pnl = pd.Series(world["pf"].pnl_under_shocks(r.shocks))
    return r


def test_slow_markets_scale_the_loss_and_cost_more_together(world):
    """Starving one name of volume makes it slower than 10 days: its scenario P&L scales by √(H/10), its
    trades share the volume (the market is slower than any of them), and the pooled exit costs at least
    as much as the trades one by one when they all point the same way."""
    pf = world["pf"]
    names = pd.Series(
        {
            t: pf.by_id[t].instrument.ticker
            for t in pf.priced_ids
            if pf.by_id[t].product_type.value == "CASH_EQUITY"
        }
    )
    name = names.value_counts().index[0]
    liq = world["liq"].copy()
    starved = liq["trade_id"].isin(names[names == name].index)
    liq.loc[starved, "adv"] = liq.loc[starved, "adv"] / 1000.0
    r = _with_pnl(world, _usd_rates_result(world, -1.0, 200.0))
    eq = {f: -0.2 for f in pf.base.values if f == f"EQ:{name}"}
    r_eq = SimpleNamespace(scenario=SimpleNamespace(scenario_id="name_down", name="name down"), shocks=eq)
    res = _run(world, liq, [r, _with_pnl(world, r_eq)])
    m = res.markets.set_index("market").loc[f"CASH_EQUITY:{name}"]
    assert m["horizon_days"] > 10 and m["loss_scale"] == pytest.approx(
        min(np.sqrt(m["horizon_days"] / 10), np.sqrt(12)), rel=1e-9
    )
    assert m["horizon_days"] >= m["trade_horizon_days"] * m["net_share"] - 1e-9
    s = res.scenarios.set_index("scenario_id").loc["name_down"]
    plain = float(r_eq.pnl[r_eq.pnl.index.isin(names[names == name].index)].sum())
    assert s["horizon_addon"] == pytest.approx(plain * (m["loss_scale"] - 1.0), rel=1e-6, abs=1.0)
    assert s["liquidation_loss"] == pytest.approx(s["adjusted_pnl"] - s["exit_cost"])
    same_way = res.markets[res.markets["net_share"] > 1 - 1e-12]
    assert (
        same_way["exit_cost"] >= same_way["trade_view_cost"] - 1e-6 * same_way["trade_view_cost"].abs() - 1.0
    ).all()


def test_fast_markets_do_not_scale_and_tables_hold_together(world):
    res = _run(world, results=[_with_pnl(world, _usd_rates_result(world))])
    m = res.markets
    assert (m.loc[m["horizon_days"] <= 10, "loss_scale"] == 1.0).all()
    assert (m["loss_scale"] <= np.sqrt(12) + 1e-12).all()
    assert list(m.columns) == ls.MARKET_COLUMNS and list(res.scenarios.columns) == ls.SCENARIO_COLUMNS
    d = res.desks
    assert d["pnl"].sum() == pytest.approx(res.scenarios["pnl"].sum(), rel=1e-9, abs=1.0)
    assert d["exit_cost"].sum() == pytest.approx(m["exit_cost"].sum(), rel=1e-6)
    assert np.allclose(m["concentration_addon"], m["exit_cost"] - m["trade_view_cost"])
    s = res.summary()
    assert s["liquidation_exit_cost"] == pytest.approx(m["exit_cost"].sum())
    if len(res.sectors):
        assert set(res.sectors["direction"]) <= {"UP", "DOWN"} and (res.sectors["names"] >= 2).all()
