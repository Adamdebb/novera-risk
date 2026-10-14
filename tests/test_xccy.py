"""Cross-currency basis and collateral discounting (decision round 35)."""

from datetime import date

import numpy as np
import pytest
import QuantLib as ql

from novera.domain import (
    CSA,
    BuySell,
    ClearingType,
    DayCount,
    Frequency,
    FXForward,
    InterestRateSwap,
    NettingSet,
    SwapSide,
    Trade,
)
from novera.market_data import MarketSnapshot
from novera.pricing import PRICERS
from novera.pricing.collateral import CollateralMap
from novera.pricing.valuation import value_trade
from novera.risk import DependencyIndex
from tests.test_pricing import AS_OF, EUR_ZEROS, TENOR_LABELS, USD_ZEROS, _market, _ql_curve

XCCY_TENORS = ("1Y", "2Y", "5Y", "10Y", "30Y")


@pytest.fixture(scope="module")
def market() -> MarketSnapshot:
    """EUR basis -15bp flat, JPY -40bp flat, a JPY curve and USD/JPY spot."""
    base = _market()
    values = dict(base.values)
    values.update({f"XCCY:EUR:{t}": -0.0015 for t in XCCY_TENORS})
    values.update({f"XCCY:JPY:{t}": -0.0040 for t in XCCY_TENORS})
    values.update({f"IR:JPY:{t}": 0.005 for t in TENOR_LABELS})
    values["FX:USDJPY"] = 150.0
    return MarketSnapshot(as_of=AS_OF, values=values)


def _bilateral_trade(trade_id: str, instrument, **kw) -> Trade:
    return Trade(
        trade_id=trade_id,
        instrument=instrument,
        direction=kw.pop("direction", BuySell.BUY),
        quantity=kw.pop("quantity", 100e6),
        trade_price=kw.pop("trade_price", 0.0),
        trade_date=AS_OF,
        book_id="B",
        trader_id="T",
        counterparty_id="BANK_A",
        clearing=ClearingType.BILATERAL,
        netting_set_id=kw.pop("netting_set_id", "NS_USD"),
        **kw,
    )


def _collateral() -> CollateralMap:
    csa = CSA(
        csa_id="CSA_USD", collateral_currency="USD",
        threshold_we_post=0.0, threshold_they_post=0.0, minimum_transfer_amount=0.0,
    )  # fmt: skip
    sets = [
        NettingSet(netting_set_id="NS_USD", counterparty_id="BANK_A", legal_entity_id="LE", csa_id="CSA_USD"),
        NettingSet(netting_set_id="NS_NONE", counterparty_id="BANK_A", legal_entity_id="LE", csa_id=None),
    ]
    return CollateralMap.from_reference(sets, [csa])


def test_collateral_curve_adds_the_basis_and_triangulates(market: MarketSnapshot) -> None:
    """EUR cash flows under USD collateral discount on ESTR plus the EUR basis; under JPY
    collateral, on ESTR plus the EUR basis less the JPY one; own or no collateral is ESTR."""
    ois = market.zero_curve("EUR")
    assert market.collateral_curve("EUR", None) is ois and market.collateral_curve("EUR", "EUR") is ois
    usd = market.collateral_curve("EUR", "USD")
    np.testing.assert_allclose(usd.zeros - ois.zeros, -0.0015)
    jpy = market.collateral_curve("EUR", "JPY")
    np.testing.assert_allclose(jpy.zeros - ois.zeros, -0.0015 + 0.0040)
    assert market.collateral_curve("USD", "USD") is market.zero_curve("USD")
    assert market.has_xccy("EUR") and market.has_xccy("USD") and not market.has_xccy("GBP")


def test_fx_forward_carries_the_basis_and_collateral_only_discounts(market: MarketSnapshot) -> None:
    """Forward = S DF_EUR^USD / DF_USD: the EUR basis lowers the EUR implied rate, so EUR/USD
    forward points rise above covered interest parity on the OIS curves. The trade's own
    collateral changes the discounting of the USD settlement amount only."""
    settle = date(2027, 9, 13)
    fwd_ins = FXForward(
        instrument_id="F", currency="USD", pair="EUR/USD", settlement_date=settle, forward_rate=1.12
    )
    t = (settle - AS_OF).days / 365
    cip = 1.10 * float(market.zero_curve("EUR").df(t)) / float(market.zero_curve("USD").df(t))
    with_basis = (
        1.10 * float(market.collateral_curve("EUR", "USD").df(t)) / float(market.zero_curve("USD").df(t))
    )
    under_csa = value_trade(_bilateral_trade("FX1", fwd_ins, quantity=10e6), market, AS_OF, _collateral())
    assert under_csa.details["forward"] == pytest.approx(with_basis, rel=1e-12)
    assert with_basis > cip and under_csa.details["xccy_applied"] == 1.0
    no_csa = value_trade(
        _bilateral_trade("FX2", fwd_ins, quantity=10e6, netting_set_id="NS_NONE"),
        market,
        AS_OF,
        _collateral(),
    )
    assert no_csa.details["forward"] == under_csa.details["forward"]  # USD settlement: same discount
    gbp = MarketSnapshot(as_of=AS_OF, values={**market.values, "FX:GBPUSD": 1.27})
    gbp = gbp.with_values({f"IR:GBP:{x}": float(z) for x, z in zip(TENOR_LABELS, USD_ZEROS, strict=True)})
    ins = fwd_ins.model_copy(update={"pair": "GBP/USD", "instrument_id": "F3"})
    assert PRICERS[ins.product_type](_bilateral_trade("FX3", ins), gbp, AS_OF).details["xccy_applied"] == 0.0


def test_eur_swap_under_usd_csa_matches_quantlib(market: MarketSnapshot) -> None:
    """A EURIBOR-6M swap under a USD-cash CSA discounts on ESTR plus the EUR/USD basis and
    projects off ESTR plus the 6M basis (QuantLib with the same two curves); cleared, it
    discounts on ESTR. The collateral moves PV, not the projected coupons."""
    tenor_basis = 0.0020
    mk = market.with_values({f"IRBASIS:EUR:6M:{x}": tenor_basis for x in XCCY_TENORS})
    swap = InterestRateSwap(
        instrument_id="S_EUR_CSA",
        currency="EUR",
        effective_date=AS_OF,
        maturity_date=date(2033, 9, 11),
        fixed_rate=0.03,
        float_index="EUR-EURIBOR-6M",
        fixed_frequency=Frequency.ANNUAL,
        fixed_day_count=DayCount.THIRTY_360,
        float_frequency=Frequency.SEMI_ANNUAL,
        float_day_count=DayCount.ACT_360,
    )
    trade = _bilateral_trade("S1", swap, swap_side=SwapSide.RECEIVE_FIXED, trade_price=0.03)
    res = value_trade(trade, mk, AS_OF, _collateral())
    own = PRICERS[swap.product_type](trade, mk, AS_OF)
    disc, proj = _ql_curve(EUR_ZEROS - 0.0015, AS_OF), _ql_curve(EUR_ZEROS + tenor_basis, AS_OF)
    index = ql.IborIndex(
        "E6M", ql.Period(6, ql.Months), 0, ql.EURCurrency(), ql.NullCalendar(), ql.Unadjusted, False,
        ql.Actual360(), proj,
    )  # fmt: skip
    fixed = ql.Schedule(
        ql.Date(11, 9, 2026), ql.Date(11, 9, 2033), ql.Period(ql.Annual), ql.NullCalendar(),
        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False,
    )  # fmt: skip
    flt = ql.Schedule(
        ql.Date(11, 9, 2026), ql.Date(11, 9, 2033), ql.Period(ql.Semiannual), ql.NullCalendar(),
        ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, False,
    )  # fmt: skip
    qs = ql.VanillaSwap(
        ql.VanillaSwap.Receiver,
        100e6,
        fixed,
        0.03,
        ql.Thirty360(ql.Thirty360.USA),
        flt,
        index,
        0.0,
        ql.Actual360(),
    )
    qs.setPricingEngine(ql.DiscountingSwapEngine(disc))
    assert res.pv_local == pytest.approx(qs.NPV(), abs=2_000)
    assert res.pv_local != pytest.approx(own.pv_local, abs=1_000)
    assert [f.amount for f in res.cashflows] == pytest.approx([f.amount for f in own.cashflows])


def test_collateral_map_and_dependencies() -> None:
    """The map reads the CSA table: a netting set with a USD CSA posts USD, one without posts
    nothing, a cleared trade (no netting set) discounts on its own OIS. A EUR swap under the USD
    CSA reprices when the EUR basis moves; the same swap cleared does not; an FX forward always
    does, through its forward."""
    cmap = _collateral()
    swap = InterestRateSwap(
        instrument_id="S", currency="EUR", effective_date=AS_OF, maturity_date=date(2031, 9, 11),
        fixed_rate=0.025, float_index="EUR-EURIBOR-6M",
    )  # fmt: skip
    bil = _bilateral_trade("S_BIL", swap, swap_side=SwapSide.PAY_FIXED)
    cleared = bil.model_copy(
        update={
            "trade_id": "S_CCP",
            "clearing": ClearingType.CLEARED,
            "netting_set_id": None,
            "counterparty_id": "LCH",
        }
    )
    fx = _bilateral_trade(
        "FX_DEP",
        FXForward(
            instrument_id="F",
            currency="JPY",
            pair="USD/JPY",
            settlement_date=date(2027, 3, 11),
            forward_rate=148.0,
        ),
        netting_set_id="NS_NONE",
    )
    assert cmap.for_trade(bil) == "USD" and cmap.for_trade(cleared) is None and cmap.for_trade(fx) is None
    idx = DependencyIndex.build([bil, cleared, fx], "USD", cmap)
    assert idx.trades_for(["XCCY:EUR:5Y"]) == {"S_BIL"}
    assert idx.trades_for(["XCCY:JPY:1Y"]) == {"FX_DEP"}
