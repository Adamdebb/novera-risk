from datetime import date

import pandas as pd
import pytest

from novera.domain import HierarchyLevel, Limit, LimitScope, LimitType
from novera.limits import RiskInputs, monitor, status_of
from novera.market_data.history import MarketHistory
from novera.pricing.valuation import value_portfolio
from novera.risk import (
    HYPOTHETICAL_LIBRARY,
    Portfolio,
    VaRConfig,
    compute_sensitivities,
    historical_var,
    run_stress,
)
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)
from novera.simulation.market_data import MarketSimConfig, generate_market_data

AS_OF = date(2026, 9, 11)


@pytest.fixture(scope="module")
def inputs():
    org = build_global_macro_bank()
    cp = build_counterparty_universe(org)
    md = generate_market_data(MarketSimConfig(end_date=AS_OF, years=1.5, seed=31))
    hist = MarketHistory.from_long(md.history)
    gen = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=AS_OF, n_trades=150, seed=31, market_history=hist)
    )
    universe = {f.factor_id: f for f in md.universe}
    val = value_portfolio(gen.snapshot, md.previous_snapshot, org, "USD").table
    pf = Portfolio(gen.snapshot.trades, md.previous_snapshot, "USD", universe=universe)
    sens = compute_sensitivities(pf)
    var = historical_var(pf, hist, VaRConfig(window_days=200))
    stress = run_stress(pf, HYPOTHETICAL_LIBRARY[:4])
    return RiskInputs(val, sens, var, stress)


def _limit(lid, lt, level, entity, amount, **filters):
    return Limit(
        limit_id=lid,
        limit_type=lt,
        scope=LimitScope(level=level, entity_id=entity, **filters),
        amount=amount,
        owner="Test",
        effective_from=date(2026, 1, 1),
    )


def test_status_thresholds():
    assert status_of(0.5, 0.8) == "OK"
    assert status_of(0.85, 0.8) == "WARNING"
    assert status_of(1.0, 0.8) == "BREACH"
    assert status_of(float("nan"), 0.8) == "NO_DATA"


def test_var_limit_firm_equals_total(inputs):
    lim = _limit("L1", LimitType.VAR, HierarchyLevel.FIRM, "GMB", 1e12)
    out = monitor([lim], inputs, AS_OF)
    assert out.loc[0, "current"] == pytest.approx(inputs.var.var)
    assert out.loc[0, "status"] == "OK"
    tight = _limit("L2", LimitType.VAR, HierarchyLevel.FIRM, "GMB", inputs.var.var * 0.9)
    assert monitor([tight], inputs, AS_OF).loc[0, "status"] == "BREACH"


def test_desk_dv01_and_tenor_filter(inputs):
    s = inputs.sensitivities.merge(inputs.valuation[["trade_id", "desk_id"]], on="trade_id")
    usd = s[(s.measure == "DV01") & (s.desk_id == "USD_RATES") & (s.underlying == "USD")]
    total = abs(usd["value"].sum())
    ten = abs(usd[usd.bucket == "10Y"]["value"].sum())
    lim_all = _limit("D1", LimitType.DV01, HierarchyLevel.DESK, "USD_RATES", 1e9, currency="USD")
    lim_10y = _limit(
        "D2", LimitType.DV01, HierarchyLevel.DESK, "USD_RATES", 1e9, currency="USD", tenor_bucket="10Y"
    )
    out = monitor([lim_all, lim_10y], inputs, AS_OF).set_index("limit_id")
    assert out.loc["D1", "current"] == pytest.approx(total)
    assert out.loc["D2", "current"] == pytest.approx(ten)


def test_stress_loss_is_worst_case(inputs):
    lim = _limit("S1", LimitType.STRESS_LOSS, HierarchyLevel.FIRM, "GMB", 1e12)
    out = monitor([lim], inputs, AS_OF)
    worst = min(r.total for r in inputs.stress)
    assert out.loc[0, "current"] == pytest.approx(max(-worst, 0.0))


def test_concentration_is_a_share(inputs):
    lim = _limit(
        "C1",
        LimitType.CONCENTRATION,
        HierarchyLevel.DESK,
        "USD_RATES",
        0.5,
        currency="USD",
        tenor_bucket="10Y",
    )
    out = monitor([lim], inputs, AS_OF)
    assert 0.0 <= out.loc[0, "current"] <= 1.0


def test_counterparty_exposure_non_negative(inputs):
    lim = _limit("X1", LimitType.COUNTERPARTY_EXPOSURE, HierarchyLevel.COUNTERPARTY, "BANK_A", 1e9)
    out = monitor([lim], inputs, AS_OF)
    assert out.loc[0, "current"] >= 0


def test_expired_limits_are_skipped(inputs):
    lim = Limit(
        limit_id="E",
        limit_type=LimitType.VAR,
        scope=LimitScope(level=HierarchyLevel.FIRM, entity_id="GMB"),
        amount=1.0,
        owner="x",
        effective_from=date(2020, 1, 1),
        effective_to=date(2020, 12, 31),
    )
    assert monitor([lim], inputs, AS_OF).empty
    assert isinstance(pd.DataFrame(), pd.DataFrame)
