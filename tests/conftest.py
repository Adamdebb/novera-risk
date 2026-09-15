from __future__ import annotations

from datetime import date

import pytest

from novera.domain import (
    CSA,
    Book,
    Business,
    BuySell,
    CashEquity,
    ClearingType,
    Counterparty,
    CounterpartyType,
    Desk,
    Firm,
    GovernmentBond,
    InterestRateSwap,
    LegalEntity,
    NettingSet,
    Organisation,
    PortfolioSnapshot,
    SwapSide,
    Trade,
    Trader,
)
from novera.domain.enums import AssetClass

BUSINESS_DATE = date(2026, 9, 11)


@pytest.fixture
def organisation() -> Organisation:
    return Organisation(
        firm=Firm(firm_id="GMB", name="Global Macro Bank", firm_type="INVESTMENT_BANK"),
        legal_entities=(
            LegalEntity(
                legal_entity_id="GMB_NY",
                firm_id="GMB",
                name="GMB New York",
                jurisdiction="US",
                functional_currency="USD",
            ),
            LegalEntity(
                legal_entity_id="GMB_LN",
                firm_id="GMB",
                name="GMB London",
                jurisdiction="GB",
                functional_currency="GBP",
            ),
        ),
        businesses=(Business(business_id="MACRO", firm_id="GMB", name="Macro"),),
        desks=(
            Desk(
                desk_id="USD_RATES",
                business_id="MACRO",
                name="USD Rates",
                asset_class=AssetClass.RATES,
                region="AMER",
            ),
            Desk(
                desk_id="INDEX_EQ",
                business_id="MACRO",
                name="Index Equity",
                asset_class=AssetClass.EQUITY,
                region="EMEA",
            ),
        ),
        books=(
            Book(book_id="USD_MACRO_RV", desk_id="USD_RATES", legal_entity_id="GMB_NY", name="USD Macro RV"),
            Book(book_id="EU_INDEX", desk_id="INDEX_EQ", legal_entity_id="GMB_LN", name="European Index"),
        ),
        traders=(
            Trader(trader_id="T01", desk_id="USD_RATES", name="Trader 01"),
            Trader(trader_id="T02", desk_id="INDEX_EQ", name="Trader 02"),
        ),
    )


@pytest.fixture
def counterparty() -> Counterparty:
    return Counterparty(
        counterparty_id="BANK_A",
        name="Bank A",
        counterparty_type=CounterpartyType.BANK,
        country="US",
        rating="A+",
    )


@pytest.fixture
def csa() -> CSA:
    return CSA(
        csa_id="CSA_BANK_A",
        collateral_currency="USD",
        threshold_we_post=0,
        threshold_they_post=5_000_000,
        minimum_transfer_amount=500_000,
    )


@pytest.fixture
def netting_set(counterparty: Counterparty, csa: CSA) -> NettingSet:
    return NettingSet(
        netting_set_id="NS_BANK_A_NY",
        counterparty_id=counterparty.counterparty_id,
        legal_entity_id="GMB_NY",
        csa_id=csa.csa_id,
    )


@pytest.fixture
def swap_trade(netting_set: NettingSet) -> Trade:
    return Trade(
        trade_id="IRS_000183",
        instrument=InterestRateSwap(
            instrument_id="USD_IRS_5Y_2031",
            currency="USD",
            effective_date=date(2026, 9, 15),
            maturity_date=date(2031, 9, 15),
            fixed_rate=0.0385,
            float_index="USD-SOFR",
        ),
        direction=BuySell.BUY,
        swap_side=SwapSide.RECEIVE_FIXED,
        quantity=250_000_000,
        trade_price=0.0385,
        trade_date=date(2026, 9, 11),
        book_id="USD_MACRO_RV",
        trader_id="T01",
        counterparty_id="BANK_A",
        clearing=ClearingType.BILATERAL,
        netting_set_id=netting_set.netting_set_id,
    )


@pytest.fixture
def bond_trade() -> Trade:
    return Trade(
        trade_id="BOND_000001",
        instrument=GovernmentBond(
            instrument_id="UST_4.25_2036",
            currency="USD",
            issuer="US Treasury",
            coupon_rate=0.0425,
            issue_date=date(2026, 5, 15),
            maturity_date=date(2036, 5, 15),
        ),
        direction=BuySell.SELL,
        quantity=50_000_000,
        trade_price=99.5,
        trade_date=date(2026, 9, 10),
        settlement_date=date(2026, 9, 11),
        book_id="USD_MACRO_RV",
        trader_id="T01",
        counterparty_id="EXCH_DTC",
        clearing=ClearingType.EXCHANGE,
    )


@pytest.fixture
def equity_trade() -> Trade:
    return Trade(
        trade_id="EQ_000042",
        instrument=CashEquity(
            instrument_id="SAP_GY",
            currency="EUR",
            ticker="SAP",
            exchange="XETRA",
            sector="Technology",
            country="DE",
        ),
        direction=BuySell.BUY,
        quantity=120_000,
        trade_price=214.3,
        trade_date=date(2026, 9, 11),
        settlement_date=date(2026, 9, 15),
        book_id="EU_INDEX",
        trader_id="T02",
        counterparty_id="EXCH_XETRA",
        clearing=ClearingType.EXCHANGE,
    )


@pytest.fixture
def snapshot(swap_trade: Trade, bond_trade: Trade, equity_trade: Trade) -> PortfolioSnapshot:
    return PortfolioSnapshot(business_date=BUSINESS_DATE, trades=(swap_trade, bond_trade, equity_trade))
