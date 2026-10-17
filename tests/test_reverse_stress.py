"""Reverse stress in PCA space and the stressed PCA Monte Carlo (MR-021, MR-022, round 50)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.domain.enums import HierarchyLevel
from novera.domain.limits import Limit, LimitScope, LimitType
from novera.market_data.history import MarketHistory
from novera.pricing.valuation import value_portfolio
from novera.risk import Portfolio, compute_sensitivities
from novera.risk import pca_space as ps
from novera.risk import reverse_stress as rs
from novera.risk import stress_monte_carlo as smc
from novera.risk.var import taylor_pnl_matrix
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def world():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=2.5, seed=50))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=300, seed=50, market_history=hist)
    )
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    book = ps.quad_book(pf, sens, list(pf.base.values))
    space = ps.build_space(pf, hist, book, None, list(pf.priced_ids))
    return {"pf": pf, "sens": sens, "val": val, "book": book, "space": space, "hist": hist}


def _limit(limit_id: str, level: HierarchyLevel, entity: str, amount: float) -> Limit:
    return Limit(
        limit_id=limit_id,
        limit_type=LimitType.STRESS_LOSS,
        scope=LimitScope(level=level, entity_id=entity),
        amount=amount,
        owner="Head of Market Risk",
        effective_from=date(2026, 1, 1),
    )


def test_quadratic_book_equals_the_challenger_approximation(world):
    pf, sens, book = world["pf"], world["sens"], world["book"]
    x = np.random.default_rng(3).normal(size=(4, len(book.factors))) * 0.01
    ref = taylor_pnl_matrix(pf, sens, pd.DataFrame(x, columns=book.factors)).sum(axis=1).to_numpy()
    lin, quad = book.scope(pf.priced_ids)
    assert book.pnl(x, lin, quad) == pytest.approx(ref, rel=1e-9)


def test_space_keeps_the_variance_and_the_tail(world):
    sp = world["space"]
    assert sp.explained >= ps.VARIANCE_TARGET - 1e-9
    assert sp.tail["tail_capture"].iloc[-1] >= ps.TAIL_TARGET or sp.k >= min(
        ps.MAX_COMPONENTS, len(sp.eigenvalues)
    )
    assert (np.diff(sp.eigenvalues) <= 1e-12).all()
    # scores of a factor move reproduce the move inside the kept space
    x = sp._moves[:20]
    back = sp.shocks(sp.scores(x))
    again = sp.shocks(sp.scores(back))
    assert again == pytest.approx(back, rel=1e-9, abs=1e-12)
    # unit variance by construction on the history the space was fitted on
    assert sp.scores(sp._moves).std(axis=0).mean() == pytest.approx(1.0, abs=0.35)


def test_solution_reaches_the_target_with_the_smallest_norm(world):
    book, sp = world["book"], world["space"]
    p = rs.problem(book, sp, list(world["pf"].priced_ids))
    target = 300e6
    s = rs.solve(p, target, [])
    assert s is not None and p.loss(s) == pytest.approx(target, rel=1e-6)
    rng = np.random.default_rng(4)
    for _ in range(300):  # no other direction gets there on a shorter vector
        d = rng.standard_normal(len(s))
        d /= np.linalg.norm(d)
        t = rs.hit(p, d, target)
        if t is not None:
            assert t >= np.linalg.norm(s) - 1e-6


def test_further_solutions_are_different_stories(world):
    book, sp = world["book"], world["space"]
    p = rs.problem(book, sp, list(world["pf"].priced_ids))
    first = rs.solve(p, 300e6, [])
    second = rs.solve(p, 300e6, [first])
    assert second is not None
    cos = first @ second / (np.linalg.norm(first) * np.linalg.norm(second))
    assert cos <= np.cos(np.radians(rs.MIN_ANGLE_DEG)) + 1e-6
    assert np.linalg.norm(second) >= np.linalg.norm(first) - 1e-6
    assert p.loss(second) >= 300e6 * (1 - 1e-6)


def test_run_confirms_in_full_revaluation_and_respects_price_bounds(world):
    pf, book, sp, val = world["pf"], world["book"], world["space"], world["val"]
    limits = [_limit("FIRM_STRESS", HierarchyLevel.FIRM, "GMB", 400e6)]
    targets = rs.targets_from_limits(limits, val)
    res = rs.run_reverse_stress(pf, book, sp, targets)
    sol = res.solutions
    assert list(sol["solution"]) == [1, 2, 3] and (sol["status"] == "CONFIRMED").all()
    assert sol["full_loss"].to_numpy() == pytest.approx(400e6, rel=rs.CONFIRM_TOLERANCE)
    assert sol["refined_norm"].to_numpy() == pytest.approx((sol["norm"] * sol["scale"]).to_numpy())
    assert (sol["probability"] > 0).all() and sol["probability"].iloc[0] == sol["probability"].max()
    # the most plausible scenario is nearest: a more extreme loss is further away
    bigger = rs.run_reverse_stress(
        pf, book, sp, rs.targets_from_limits([_limit("F", HierarchyLevel.FIRM, "GMB", 800e6)], val)
    )
    assert bigger.solutions["norm"].iloc[0] > sol["norm"].iloc[0]
    # independent check of the first solution's loss
    rel = {f for f, rf in pf.universe.items() if rf.shock_type != "ABSOLUTE"}
    p = rs.problem(book, sp, list(pf.priced_ids), rel)
    s1 = rs.solve(p, 400e6, [])
    x = sp.shocks(s1)[0]
    idx = [i for i, f in enumerate(sp.factors) if f in rel]
    assert x[idx].min() >= rs.PRICE_FLOOR - 1e-4
    assert {"DRIVER", "HEADLINE"} <= set(res.moves["kind"])
    assert res.summary()["reverse_firm_target"] == "FIRM_STRESS"
    assert len(res.neighbours) and (res.neighbours["angle"] == rs.NEIGHBOUR_ANGLE_DEG).all()


def test_a_target_beyond_reach_is_reported_not_forced(world):
    pf, book, sp, val = world["pf"], world["book"], world["space"], world["val"]
    targets = rs.targets_from_limits([_limit("HUGE", HierarchyLevel.FIRM, "GMB", 1e15)], val)
    res = rs.run_reverse_stress(pf, book, sp, targets)
    assert list(res.solutions["status"]) == ["UNREACHABLE"]
    assert res.summary()["reverse_stress_unreachable"] == 1


def test_only_stress_loss_limits_become_targets(world):
    val = world["val"]
    var = Limit(
        limit_id="V",
        limit_type=LimitType.VAR,
        scope=LimitScope(level=HierarchyLevel.FIRM, entity_id="GMB"),
        amount=1.0,
        owner="x",
        effective_from=date(2026, 1, 1),
    )
    desk = str(val["desk_id"].iloc[0])
    t = rs.targets_from_limits([var, _limit("D", HierarchyLevel.DESK, desk, 5e6)], val)
    assert [x.target_id for x in t] == ["D"] and t[0].level == "DESK"
    assert set(t[0].trades) == set(val.loc[val["desk_id"] == desk, "trade_id"])


def test_fat_tailed_scores_have_unit_variance_and_heavier_tails():
    rng = np.random.default_rng(1)
    g = smc.draw_scores(5, 200_000, 4.0, rng, fat=False)
    f = smc.draw_scores(5, 200_000, 4.0, np.random.default_rng(1), fat=True)
    assert f.var(axis=0) == pytest.approx(np.ones(5), rel=0.15)
    assert np.quantile(np.abs(f), 0.9999) > 1.5 * np.quantile(np.abs(g), 0.9999)


def test_stressed_monte_carlo_is_reproducible_and_the_fat_tail_costs_more(world):
    pf, book, sp, val = world["pf"], world["book"], world["space"], world["val"]
    v = val.drop_duplicates("trade_id")
    priced = set(pf.priced_ids)
    scopes = {str(d): [t for t in g["trade_id"] if t in priced] for d, g in v.groupby("desk_id")}
    targets = rs.targets_from_limits([_limit("F", HierarchyLevel.FIRM, "GMB", 400e6)], val)
    a = smc.run_stress_monte_carlo(pf, book, sp, scopes, targets, paths=4000)
    b = smc.run_stress_monte_carlo(pf, book, sp, scopes, targets, paths=4000)
    pd.testing.assert_frame_equal(a.summary_table, b.summary_table)
    firm = a.summary_table[a.summary_table["scope"] == smc.FIRM].set_index("distribution")
    assert firm.loc["FAT_TAIL", "var_999"] > firm.loc["GAUSSIAN", "var_999"]
    assert firm.loc["GAUSSIAN", "tail_ratio"] == pytest.approx(1.0)
    q975 = a.quantiles.set_index("quantile").loc[0.975]
    assert (
        firm.loc["FAT_TAIL", "es_975"] >= q975["loss_fat_tail"] - 1e-6
    )  # ES at 97.5% is the mean beyond its VaR
    q = a.quantiles
    assert q["loss_gaussian"].is_monotonic_increasing and q["loss_fat_tail"].is_monotonic_increasing
    t = a.targets.iloc[0]
    assert t["prob_fat_tail"] >= t["prob_gaussian"]
    assert a.summary()["stress_mc_var_999"] == pytest.approx(firm.loc["FAT_TAIL", "var_999"])
