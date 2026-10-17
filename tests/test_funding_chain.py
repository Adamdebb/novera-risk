"""Margin, collateral and funding chain with the liquidity spiral (MR-024, round 52)."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest
from scipy import sparse

from novera.risk import funding_chain as fc
from novera.risk import stress_paths as sp
from tests.test_stress_paths import _usd_rates_result, build_world

AS_OF = date(2026, 9, 11)


def _walk(inputs, resources=None, scenario=None, sets=(), sigma0=None, loadings=None, days=3):
    n = len(inputs.trades)
    sets = list(sets)
    rows = [(sets.index(k), j) for j, k in enumerate(inputs.netting_set) if k in sets]
    S = sparse.csr_matrix(  # noqa: N806
        (np.ones(len(rows)), ([r for r, _ in rows], [c for _, c in rows])), shape=(len(sets), n)
    )
    rules = fc.FundingRules(resources or fc.FundingResources(cash_buffer=100.0))
    ld = loadings or [np.ones((n, 1)) for _ in range(days + 1)]
    sig = sigma0 if sigma0 is not None else np.sqrt((np.asarray(S @ ld[0]) ** 2).sum(axis=1))
    return fc.FundingWalk(
        inputs, rules, scenario or fc.FundingScenario("NORMAL"), ld, S, sets, sig, [], np.zeros(0)
    )


def _inputs(**kw):
    base = {
        "face": "bank",
        "as_of": AS_OF,
        "trades": ["A", "B"],
        "pv": np.array([0.0, 100.0]),
        "regime": np.array(["VM", "FUNDED"]),
        "netting_set": np.array(["", ""]),
        "hqla_haircut": np.array([np.nan, np.nan]),
    }
    return fc.FundingInputs(**{**base, **kw})


def _close(w, t, pnl, cum=None, h=None, sell=None):
    n = len(pnl)
    h = np.ones(n) if h is None else h
    return w.close(
        t,
        h,
        h,
        np.asarray(pnl, float),
        np.asarray(cum if cum is not None else pnl, float),
        0.0,
        0.0,
        np.zeros((0, 0)),
        sell,
    )


def test_rules_round_trip_and_validate():
    r = fc.FundingRules()
    assert fc.FundingRules.from_list(r.to_list()) == r
    assert r.problems() == []
    assert [s.scope for s in r.scenarios] == list(fc.FUNDING_SCENARIOS)
    bad = fc.FundingRules(replace(fc.DEFAULT_RESOURCES, cash_buffer=-1.0), r.scenarios[:2])
    errs = bad.problems()
    assert any("cash buffer" in e for e in errs) and any("exactly" in e for e in errs)
    assert any("haircut multiplier" in e for e in replace(r.scenarios[0], haircut_multiplier=0.5).problems())
    with pytest.raises(ValueError):
        fc.FundingRules.from_list([{"scope": "NORMAL", "colour": "red"}])


def test_regimes():
    assert fc.regime_of("INTEREST_RATE_SWAP", "BUY", "CLEARED", "CCP_LCH", False, True) == "VM"
    assert fc.regime_of("GOVERNMENT_BOND", "BUY", "EXCHANGE", "EXCH_CME", False, False) == "FUNDED"
    assert fc.regime_of("CASH_EQUITY", "SELL", "EXCHANGE", "EXCH_NYSE", False, False) == "VM"  # short: borrow
    assert fc.regime_of("EQUITY_OPTION", "BUY", "EXCHANGE", "EXCH_CME", False, False) == "UNMARGINED"
    assert fc.regime_of("EQUITY_OPTION", "SELL", "EXCHANGE", "EXCH_CME", False, False) == "VM"
    assert fc.regime_of("COMMODITY_FUTURE", "BUY", "EXCHANGE", "EXCH_ICE", False, False) == "VM"
    assert fc.regime_of("INTEREST_RATE_SWAP", "BUY", "BILATERAL", "BANK_A", True, False) == "CSA"
    assert fc.regime_of("REPO", "SELL", "BILATERAL", "BANK_A", True, False) == "UNMARGINED"
    assert fc.regime_of("FX_FORWARD", "BUY", "BILATERAL", "CORP_AIR", False, False) == "UNMARGINED"


def test_a_quiet_day_moves_nothing_and_the_terms_move_from_day_one():
    """No move, no change under normal terms; a downgrade calls the threshold at once, market-wide
    haircuts cost liquidity on what is posted and on the HQLA."""
    x = _inputs(
        trades=["S", "BOND"],
        pv=np.array([-30.0, 200.0]),
        regime=np.array(["CSA", "FUNDED"]),
        netting_set=np.array(["NS", ""]),
        hqla_haircut=np.array([np.nan, 0.02]),
        csa={"NS": {"threshold_we_post": 10.0, "threshold_they_post": 10.0, "haircut": 0.02}},
    )
    out = {}
    for sc in fc.DEFAULT_SCENARIOS:
        w = _walk(x, scenario=sc, sets=["NS"])
        w.begin()
        _close(w, 1, [0.0, 0.0])
        out[sc.scope] = w.days[0]
    assert w.start() == pytest.approx(100.0 + 200.0 * 0.98)
    n = out["NORMAL"]
    assert n.collateral == n.initial_margin == n.hqla == n.variation_margin == 0.0
    # We posted 20 (value -30 beyond our threshold of 10); a downgrade removes the threshold: 10 more,
    # and its 2% haircut.
    assert out["FIRM_SPECIFIC"].collateral == pytest.approx(-(10.0 + 10.0 * (1 / 0.98 - 1)))
    mw = out["MARKET_WIDE"]
    assert mw.collateral == pytest.approx(-(20.0 * (1 / 0.96 - 1 / 0.98)))
    assert mw.hqla == pytest.approx(-200.0 * 0.02)


def test_variation_margin_csa_and_hqla_on_a_move():
    """A cleared loss is cash at once; a CSA set calls what crosses the threshold; a bond that falls
    lowers the HQLA by its fall less the haircut."""
    x = _inputs(
        trades=["CLR", "S", "BOND"],
        pv=np.array([0.0, -5.0, 100.0]),
        regime=np.array(["VM", "CSA", "FUNDED"]),
        netting_set=np.array(["", "NS", ""]),
        hqla_haircut=np.array([np.nan, np.nan, 0.02]),
        csa={"NS": {"threshold_we_post": 10.0, "threshold_they_post": 0.0, "haircut": 0.0}},
    )
    w = _walk(x, sets=["NS"])
    w.begin()
    start = w.start()
    _close(w, 1, [-7.0, -15.0, -10.0])
    d = w.days[0]
    assert d.variation_margin == pytest.approx(-7.0)
    assert d.collateral == pytest.approx(-10.0)  # value -20: we post 10 beyond the threshold
    assert d.hqla == pytest.approx(-10.0 * 0.98)
    assert d.net_liquidity == pytest.approx(start - 7.0 - 10.0 - 9.8)


def test_initial_margin_follows_the_stressed_risk_and_ccps_are_procyclical():
    """Posted IM scales with the set's risk at the stressed point; a bilateral counterparty's margin
    above the threshold is pro rata; a CCP also reads the realised volatility, capped."""
    x = _inputs(
        trades=["BIL", "CCP"],
        pv=np.array([0.0, 0.0]),
        regime=np.array(["CSA", "VM"]),
        netting_set=np.array(["NS_BIL", "NS_CCP"]),
        csa={"NS_BIL": {"threshold_we_post": 0.0, "threshold_they_post": 0.0, "haircut": 0.0}},
        im_posted_model={"NS_BIL": 60.0, "NS_CCP": 40.0},
        im_counterparty={"NS_BIL": "BANK", "NS_CCP": "CCP_LCH"},
        ccp_sets=frozenset({"NS_CCP"}),
        im_threshold=50.0,
    )
    res = fc.FundingResources(cash_buffer=1000.0, im_collateral_haircut=0.0, ccp_margin_cap=2.0)
    # Day 1: the bilateral set's risk doubles (gamma); the CCP's does not, but it has a 10-sigma day.
    loadings = [np.ones((2, 1)), np.array([[2.0], [1.0]])]
    w = _walk(x, res, sets=["NS_BIL", "NS_CCP"], loadings=loadings, days=1)
    w.begin()
    _close(w, 1, [0.0, 10.0])
    # Bilateral: 60 -> 120 model, posted above 50: 10 -> 70. CCP: 40 x min(sqrt(0.94 + 0.06 x 100), 2) = 80.
    assert w.days[0].initial_margin == pytest.approx(-((70.0 - 10.0) + (80.0 - 40.0)))
    w2 = _walk(
        x,
        res,
        scenario=fc.FundingScenario("NORMAL", ccp_procyclical=False),
        sets=["NS_BIL", "NS_CCP"],
        loadings=loadings,
        days=1,
    )
    w2.begin()
    _close(w2, 1, [0.0, 10.0])
    assert w2.days[0].initial_margin == pytest.approx(-60.0)


def test_a_repo_takes_the_add_on_when_it_rolls():
    x = _inputs(
        repos=[
            {"cash": 1000.0, "haircut": 0.02, "end_date": AS_OF + timedelta(days=4)},  # rolls on day 2
            {"cash": 500.0, "haircut": 0.02, "end_date": AS_OF + timedelta(days=60)},  # term
            {"cash": -300.0, "haircut": 0.02, "end_date": AS_OF + timedelta(days=1)},  # cash lent
        ]
    )
    w = _walk(x, scenario=fc.FundingScenario("FIRM_SPECIFIC", repo_haircut_addon=0.05))
    w.begin()
    for t in (1, 2, 3):
        _close(w, t, [0.0, 0.0])
    assert [round(d.hqla, 9) for d in w.days] == [0.0, -50.0, -50.0]


class _Stub:
    """A seller for one VM trade A and one HQLA bond B in the same desk: selling B pays 1% and a
    square-root impact 20·Q^1.5 on the day's cumulative sale Q, which re-marks A (margined) by
    10·√Q per unit; each round pays the increment."""

    def __init__(self, room=1.0):
        self._room, self.q = room, 0.0

    def room(self, h):
        return np.minimum(np.array([0.0, self._room - self.q]), h)

    def trade(self, dh):
        q0, self.q = self.q, self.q + dh[1]
        return np.array([0.0, dh[1]]), np.array([10.0 * (np.sqrt(self.q) - np.sqrt(q0)), 0.0])


def _spiral_inputs():
    return _inputs(hqla_haircut=np.array([np.nan, 0.5]))


def test_the_spiral_sells_to_a_fixed_point_and_impact_makes_it_sell_more():
    """A margined loss leaves liquidity short; selling the bond raises its haircut share; with
    permanent impact the sale re-marks the margined trade, so the firm must sell more, in more rounds."""
    out = {}
    for share in (0.0, 1.0):
        res = fc.FundingResources(cash_buffer=10.0, permanent_impact_share=share)
        w = _walk(_spiral_inputs(), res)
        w.begin()
        assert w.start() == pytest.approx(60.0)
        dh, cost, remark = _close(w, 1, [-70.0, 0.0], sell=_Stub())
        d = w.days[0]
        assert d.net_liquidity >= -fc.TOLERANCE * 60.0 and w.failed_day is None
        assert dh[1] > 0 and cost[1] == pytest.approx(dh[1])
        out[share] = (dh[1], d.spiral_rounds, d.permanent_impact)
    assert out[0.0][2] == 0.0 and out[1.0][2] > 0
    assert out[1.0][0] > out[0.0][0] and out[1.0][1] >= out[0.0][1]


def test_a_shortfall_the_market_cannot_absorb_is_the_day_the_firm_fails():
    res = fc.FundingResources(cash_buffer=10.0)
    w = _walk(_spiral_inputs(), res)
    w.begin()
    _close(w, 1, [-70.0, 0.0], sell=_Stub(room=0.01))
    _close(w, 2, [0.0, 0.0], sell=_Stub(room=0.01))
    assert w.failed_day == 1 and w.unresolved and w.days[0].net_liquidity < 0
    frozen = _walk(_spiral_inputs(), res)
    frozen.begin()
    _close(frozen, 1, [-70.0, 0.0])
    assert frozen.failed_day == 1 and not frozen.unresolved and frozen.days[0].forced_sold == 0


def test_fund_liquidity_is_nav_less_margin_on_the_path():
    x = _inputs(
        face="fund",
        pv=np.array([100.0, -50.0]),
        regime=np.array(["VM", "VM"]),
        nav=100.0,
        margin_total=30.0,
        margin_rate=np.array([0.2, 0.2]),
        exposure=np.array([100.0, -50.0]),
        linear=np.array([1.0, 1.0]),
    )
    w = _walk(x)
    w.begin()
    assert w.start() == pytest.approx(70.0)
    _close(w, 1, [-10.0, 0.0])  # the long falls: NAV -10, its exposure 90, margin 28
    assert w.days[0].net_liquidity == pytest.approx(100.0 - 10.0 - 28.0)
    assert w.days[0].initial_margin == pytest.approx(2.0)
    w = _walk(x, scenario=fc.FundingScenario("FIRM_SPECIFIC", pb_margin_multiplier=1.5))
    w.begin()
    _close(w, 1, [0.0, 0.0])
    assert w.days[0].net_liquidity == pytest.approx(100.0 - 45.0)


# --- on the stress paths -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world():
    return build_world()


def _world_inputs(world, cash):
    pf = world["pf"]
    trades = list(world["book"].trades)
    reg, hq = [], []
    for tid in trades:
        t = pf.by_id[tid]
        r = fc.regime_of(
            t.product_type.value, t.direction.value, t.clearing.value, t.counterparty_id, False, False
        )
        reg.append(r)
        bond = r == "FUNDED" and t.product_type.value == "GOVERNMENT_BOND"
        hq.append(0.02 if bond else np.nan)
    x = fc.FundingInputs(
        face="bank",
        as_of=pf.as_of,
        trades=trades,
        pv=np.array([pf.base_pv[t] for t in trades]),
        regime=np.array(reg),
        netting_set=np.array([""] * len(trades)),
        hqla_haircut=np.array(hq, dtype=float),
    )
    rules = fc.FundingRules(fc.FundingResources(cash_buffer=cash))
    return x, rules


def _run_funded(world, cash, rule=None):
    import pandas as pd

    x, rules = _world_inputs(world, cash)
    r = _usd_rates_result(world, 1.0, 300.0)
    r.pnl = pd.Series(world["pf"].pnl_under_shocks(r.shocks))
    res = sp.run_stress_paths(
        world["pf"],
        world["book"],
        world["space"],
        world["sens"],
        world["hist"],
        world["val"],
        world["liq"],
        dict.fromkeys(world["desks"], 1e6),
        [],
        [r],
        [rule or sp.ActionRule()],
        workers=1,
        funding=x,
        funding_rules=rules,
        all_results=[r],
    )
    return res, r


def test_with_ample_liquidity_the_managed_walk_is_the_managed_path(world):
    """No shortfall, no forced sale: each funding walk of the managed book ends on the MR-023 managed
    P&L, and the frozen walk on the frozen P&L."""
    res, _ = _run_funded(world, 1e12)
    liq = res.liquidity
    assert liq is not None and len(liq.summary_table) == 2 * 3 * len(res.summary_table) // 2
    pnl = res.summary_table.set_index(["path_id", "book"])["end_pnl"]
    for _, row in liq.summary_table.iterrows():
        assert row["forced_sold"] == 0 and row["survives"]
        assert row["end_pnl"] == pytest.approx(pnl[(row["path_id"], row["book"])], rel=1e-9, abs=1.0)
    assert set(liq.instant["funding_scenario"]) == set(fc.FUNDING_SCENARIOS)
    assert (liq.instant["stress_set"] == "LIMIT").all()
    s = res.summary()
    assert s["liquidity_failures_managed"] == 0 and s["liquidity_start"] > 1e12


def test_a_thin_buffer_forces_sales_and_the_frozen_book_runs_out_first(world):
    res, r = _run_funded(world, 0.0, sp.ActionRule(enabled=False))
    t = res.liquidity.summary_table
    fr = t[t["book"] == "FROZEN"].set_index(["path_id", "funding_scenario"])
    mg = t[t["book"] == "MANAGED"].set_index(["path_id", "funding_scenario"])
    short = fr[~fr["survives"]]
    if short.empty:
        pytest.skip("the test book has no margined loss on this path")
    for k in short.index:
        assert mg.loc[k, "forced_sale_days"] > 0
        managed = mg.loc[k, "survival_day"]
        assert np.isnan(managed) or managed >= short.loc[k, "survival_day"]
    days = res.liquidity.days
    assert list(days.columns) == fc.DAY_COLUMNS and list(t.columns) == fc.SUMMARY_COLUMNS
