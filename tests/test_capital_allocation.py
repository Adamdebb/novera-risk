"""Desk allocation of regulatory capital: Euler contributions add up, incremental and standalone
measure what they say (REG-008)."""

import numpy as np
import pandas as pd
import pytest

from novera.regulatory.allocation import assemble, ba_cva_by_desk, saccr_by_set, saccr_capital
from novera.regulatory.cva_capital import ba_cva_charge
from novera.regulatory.frtb_ima import LIQUIDITY_HORIZONS, _ima_by_desk, _ima_capital
from novera.regulatory.frtb_sa import frtb_sa
from novera.regulatory.saccr import NettingSetInputs, SACCRResult, TradeAddOn


def _dv01(trade_id: str, value: float, tenor: str = "10Y") -> dict:
    return {
        "trade_id": trade_id,
        "measure": "DV01",
        "factor_id": f"IR:USD:{tenor}",
        "bucket": tenor,
        "underlying": "USD",
        "bump": 1e-4,
        "value": value,
    }


def _val(*rows: tuple[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_id": t, "desk_id": d, "status": "LIVE", "product_type": "INTEREST_RATE_SWAP", "pv": 0.0}
            for t, d in rows
        ]
    )


def test_frtb_sa_euler_adds_up_and_a_hedge_desk_carries_negative_capital():
    sens = pd.DataFrame([_dv01("a", -1000.0), _dv01("b", 400.0, "5Y"), _dv01("c", -300.0, "2Y")])
    r = frtb_sa(sens, _val(("a", "D1"), ("b", "HEDGE"), ("c", "D3")))
    by = r.by_desk.set_index("desk_id")
    assert by["euler"].sum() == pytest.approx(r.total, rel=1e-9)
    assert by["euler_raw"].sum() == pytest.approx(r.total, rel=1e-6)  # homogeneous: no rescaling
    assert by.loc["HEDGE", "euler"] < 0 and by.loc["HEDGE", "incremental"] < 0  # removing it costs capital
    assert (by["standalone"] > 0).all() and by["standalone"].sum() > r.total  # diversification
    # Incremental is the charge less the charge without the desk.
    without = frtb_sa(sens[sens["trade_id"] != "a"], _val(("b", "HEDGE"), ("c", "D3")))
    assert by.loc["D1", "incremental"] == pytest.approx(r.total - without.total)


def _desk_pnl(seed: int, desks: list[str], n: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.standard_normal((n, len(desks))) @ np.diag([3.0, 2.0, 1.0][: len(desks)]), columns=desks
    )


def test_ima_euler_is_exact_and_matches_the_derivative_of_the_cascade():
    desks = ["D1", "D2", "D3"]
    pnl_by_h = {h: _desk_pnl(h, desks) * (1.0 / (1 + i)) for i, h in enumerate(LIQUIDITY_HORIZONS)}
    ses = _desk_pnl(7, desks) * 0.2
    mult = 1.7
    capital = _ima_capital(pnl_by_h, ses, mult)[3]
    by = _ima_by_desk(pnl_by_h, ses, mult, capital).set_index("desk_id")
    assert by["euler_raw"].sum() == pytest.approx(capital, rel=1e-12)
    # A small bump of one desk moves the capital by its Euler contribution (ES is piecewise
    # linear in the weights, so the one-sided derivative is exact away from a tail change).
    for d in desks:
        w = pd.Series(1.0, index=desks)
        w[d] += 1e-7
        bumped = _ima_capital(pnl_by_h, ses, mult, w)[3]
        assert (bumped - capital) / 1e-7 == pytest.approx(by.loc[d, "euler_raw"], rel=1e-5)
    alone = pd.Series([1.0, 0.0, 0.0], index=desks)
    assert by.loc["D1", "standalone"] == pytest.approx(_ima_capital(pnl_by_h, ses, mult, alone)[3])


def _addon(trade_id: str, notional: float, delta: float = 1.0) -> TradeAddOn:
    return TradeAddOn(trade_id, "IR", "USD", ">5y", delta, notional, 1.0, delta * notional)


def _saccr(inputs: list[NettingSetInputs], rw: float = 1.0) -> SACCRResult:
    by_cp = pd.DataFrame(
        [{"counterparty_id": c, "risk_weight": rw} for c in sorted({i.counterparty_id for i in inputs})]
    )
    return SACCRResult(pd.DataFrame(), by_cp, pd.DataFrame(), {i.netting_set_id: i for i in inputs})


def test_saccr_unmargined_set_is_homogeneous_and_removal_and_standalone_rebuild_the_set():
    ns = NettingSetInputs(
        "NS1", "CP1", None, {"a": 5e6, "b": -2e6}, [_addon("a", 100e6), _addon("b", 60e6, -1.0)]
    )
    assert ns.ead()["ead"] == pytest.approx(ns.ead({})["ead"])  # no collateral held: same set
    assert ns.ead({"a": 0.0, "b": 0.0})["ead"] == 0.0
    doubled = ns.ead({"a": 2.0, "b": 2.0})["ead"]
    assert doubled == pytest.approx(2 * ns.ead()["ead"])
    by = saccr_by_set(_saccr([ns]), {"a": "D1", "b": "D2"}).set_index("desk_id")
    ead = ns.ead()["ead"]
    assert by["euler_raw"].sum() == pytest.approx(ead, rel=1e-6) and by["euler"].sum() == pytest.approx(ead)
    assert by.loc["D2", "euler"] < 0  # the offsetting desk lowers the set's add-on
    assert by.loc["D1", "standalone"] == pytest.approx(ns.ead({"b": 0.0})["ead"])
    assert by.loc["D2", "incremental"] == pytest.approx(ead - ns.ead({"b": 0.0})["ead"])
    cap = saccr_capital(
        by.reset_index().assign(netting_set_id="NS1", counterparty_id="CP1"), _saccr([ns], 0.5)
    )
    assert cap["euler"].sum() == pytest.approx(ead * 0.5 * 0.08)


def test_saccr_floor_is_spread_by_rescaling():
    """A margined set whose replacement cost sits on the CSA floor: the floor has no derivative, so
    raw Euler falls short of the EAD and the official figure is rescaled to it."""
    from novera.domain.counterparties import CSA

    csa = CSA.model_construct(
        csa_id="C1",
        threshold_they_post=5e6,
        threshold_we_post=5e6,
        minimum_transfer_amount=0.5e6,
        independent_amount=0.0,
        haircut=0.0,
    )
    ns = NettingSetInputs("NS1", "CP1", csa, {"a": 1e6, "b": 1e6}, [_addon("a", 50e6), _addon("b", 30e6)])
    by = saccr_by_set(_saccr([ns]), {"a": "D1", "b": "D2"})
    ead = ns.ead()["ead"]
    assert by["euler_raw"].sum() < ead - 1e6 and by["euler"].sum() == pytest.approx(ead)
    assert (by["euler"] > 0).all()


def test_ba_cva_euler_adds_up_and_follows_the_counterparty_eads():
    detail = pd.DataFrame(
        [
            {"counterparty_id": "CP1", "ead": 100.0, "scva": 4.0},
            {"counterparty_id": "CP2", "ead": 50.0, "scva": 3.0},
        ]
    )
    k = ba_cva_charge(detail["scva"].to_numpy())
    sets = pd.DataFrame(
        [
            {
                "counterparty_id": "CP1",
                "desk_id": "D1",
                "euler": 70.0,
                "incremental": 60.0,
                "standalone": 80.0,
            },
            {
                "counterparty_id": "CP1",
                "desk_id": "D2",
                "euler": 30.0,
                "incremental": 20.0,
                "standalone": 40.0,
            },
            {
                "counterparty_id": "CP2",
                "desk_id": "D2",
                "euler": 50.0,
                "incremental": 50.0,
                "standalone": 50.0,
            },
        ]
    )
    by = ba_cva_by_desk(k, detail, sets).set_index("desk_id")
    assert by["euler_raw"].sum() == pytest.approx(k)
    s_less = np.array([4.0 * 40 / 100, 3.0])  # D1 out: CP1's EAD falls by its incremental 60
    assert by.loc["D1", "incremental"] == pytest.approx(k - ba_cva_charge(s_less))
    assert by.loc["D1", "standalone"] == pytest.approx(ba_cva_charge(np.array([4.0 * 0.8, 0.0])))


def test_assemble_rescales_and_totals_the_standardised_stack():
    part = pd.DataFrame(
        [
            {"desk_id": "D1", "euler_raw": 6.0, "incremental": 4.0, "standalone": 8.0},
            {"desk_id": "D2", "euler_raw": 2.0, "incremental": 1.0, "standalone": 3.0},
        ]
    )
    caps = {
        "frtb_sa": 8.0,
        "frtb_ima": 4.0,
        "saccr": 16.0,
        "ba_cva": 8.0,
        "ccp_trade": 0.0,
        "ccp_default_fund": 0.0,
    }
    a = assemble({c: part for c in caps}, caps, {"D1": "B1", "D2": "B1"})
    summ = a.summary.set_index("component")
    assert (
        summ.loc["saccr", "euler_scale"] == pytest.approx(2.0) and summ.loc["frtb_sa", "euler_scale"] == 1.0
    )
    assert a.by_desk["total_sa"].sum() == pytest.approx(8.0 + 16.0 + 8.0)
    assert set(a.rows["component"]) == {*caps, "total_sa"} and (a.rows["business_id"] == "B1").all()
