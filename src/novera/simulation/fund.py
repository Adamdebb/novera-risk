"""Template organisation: Meridian Multi-Strategy Fund (hedge-fund face).

Strategies stand in for desks, sub-strategies for books, prime brokers for bilateral
counterparties. The fund carries a NAV, an investor register with dealing terms, and a
set of planted problems: a crowded single-name long, prime-broker concentration, an
illiquid position against monthly liquidity, and a large short-vol book.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from novera.domain import (
    CSA,
    AssetClass,
    Book,
    Business,
    Counterparty,
    CounterpartyType,
    Desk,
    Firm,
    LegalEntity,
    NettingSet,
    Organisation,
    Trader,
)
from novera.simulation import reference_levels as ref
from novera.simulation.organisation import CounterpartyUniverse
from novera.simulation.trades import Template

FIRM_ID = "MSF"


class DealingFrequency(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class Investor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    investor_id: str
    name: str
    investor_type: str  # PENSION, ENDOWMENT, FOF, FAMILY_OFFICE, PRINCIPALS, SOVEREIGN_WEALTH
    share_of_nav: float = Field(gt=0, le=1)
    dealing: DealingFrequency = DealingFrequency.QUARTERLY
    notice_days: int = 60
    gate_pct: float = Field(default=0.25, ge=0, le=1, description="Max share redeemable per dealing date")
    lockup_until: date | None = None


class Fund(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    firm_id: str
    name: str
    currency: str = "USD"
    nav: float = Field(gt=0)
    inception: date
    management_fee: float = 0.015
    performance_fee: float = 0.20
    target_gross_leverage: float = 3.5
    max_gross_leverage: float = 5.0
    investors: tuple[Investor, ...]
    prime_brokers: tuple[str, ...]

    @property
    def share_check(self) -> float:
        return sum(i.share_of_nav for i in self.investors)


# strategy_id, business_id, name, asset class, region, books (book_id, legal entity, name)
_STRATEGIES: list[tuple[str, str, str, AssetClass, str, list[tuple[str, str, str]]]] = [
    (
        "GLOBAL_MACRO",
        "MACRO",
        "Global Macro Rates",
        AssetClass.RATES,
        "GLOBAL",
        [("GM_USD", "MSF_MASTER", "USD Rates RV"), ("GM_EUR_GBP", "MSF_MASTER", "EUR GBP Curve")],
    ),
    (
        "EM_MACRO",
        "MACRO",
        "EM Macro",
        AssetClass.RATES,
        "EM",
        [("EM_LOCAL", "MSF_MASTER", "EM Local Rates"), ("EM_FX_BOOK", "MSF_MASTER", "EM FX Carry")],
    ),
    (
        "FX_CARRY",
        "MACRO",
        "G10 FX Carry and Vol",
        AssetClass.FX,
        "GLOBAL",
        [("G10_CARRY", "MSF_MASTER", "G10 Carry"), ("G10_VOL", "MSF_MASTER", "G10 Vol")],
    ),
    (
        "EQ_LS_US",
        "EQUITY",
        "US Equity Long/Short",
        AssetClass.EQUITY,
        "AMER",
        [("US_TECH_LS", "MSF_MASTER", "US Tech L/S"), ("US_CORE_LS", "MSF_MASTER", "US Core L/S")],
    ),
    (
        "EQ_LS_EU",
        "EQUITY",
        "European Equity Long/Short",
        AssetClass.EQUITY,
        "EMEA",
        [("EU_LS", "MSF_MASTER", "Europe L/S")],
    ),
    (
        "INDEX_VOL_ARB",
        "EQUITY",
        "Index Volatility Arbitrage",
        AssetClass.EQUITY,
        "GLOBAL",
        [("VOL_ARB", "MSF_MASTER", "Index Vol Arb"), ("IDX_HEDGE", "MSF_MASTER", "Index Hedges")],
    ),
    (
        "CREDIT_LS",
        "CREDIT",
        "Credit Long/Short",
        AssetClass.CREDIT,
        "GLOBAL",
        [("IG_LS", "MSF_MASTER", "IG Index L/S"), ("HY_LS", "MSF_MASTER", "HY Index L/S")],
    ),
    (
        "COMMODITY_TREND",
        "SYSTEMATIC",
        "Commodity Trend",
        AssetClass.COMMODITY,
        "GLOBAL",
        [("ENERGY_TREND", "MSF_MASTER", "Energy Trend"), ("METALS_TREND", "MSF_MASTER", "Metals Trend")],
    ),
    (
        "CRYPTO_BASIS",
        "DIGITAL",
        "Crypto Basis and Directional",
        AssetClass.DIGITAL_ASSET,
        "GLOBAL",
        [("CRYPTO_BOOK", "MSF_MASTER", "Crypto")],
    ),
]
_BUSINESSES = [
    ("MACRO", "Macro"),
    ("EQUITY", "Equity"),
    ("CREDIT", "Credit"),
    ("SYSTEMATIC", "Systematic"),
    ("DIGITAL", "Digital"),
]

FUND_TEMPLATE = Template(
    name="hedge_fund",
    desk_mix={
        "GLOBAL_MACRO": {"govt_bond": 0.35, "swap": 0.45, "swaption": 0.12, "ir_future": 0.08},
        "EM_MACRO": {"govt_bond": 0.3, "swap": 0.3, "fx_forward": 0.4},
        "FX_CARRY": {"fx_spot": 0.2, "fx_forward": 0.5, "fx_option": 0.3},
        "EQ_LS_US": {"cash_equity": 0.75, "equity_option": 0.15, "etf": 0.10},
        "EQ_LS_EU": {"cash_equity": 0.9, "etf": 0.1},
        "INDEX_VOL_ARB": {"index_option": 0.6, "index_future": 0.25, "equity_exotic": 0.15},
        "CREDIT_LS": {"cds_index": 0.7, "cds_single_name": 0.3},
        "COMMODITY_TREND": {"commodity_future": 0.8, "commodity_option": 0.2},
        "CRYPTO_BASIS": {"crypto_spot": 1.0},
    },
    desk_weight={
        "GLOBAL_MACRO": 0.14,
        "EM_MACRO": 0.08,
        "FX_CARRY": 0.12,
        "EQ_LS_US": 0.22,
        "EQ_LS_EU": 0.10,
        "INDEX_VOL_ARB": 0.10,
        "CREDIT_LS": 0.08,
        "COMMODITY_TREND": 0.10,
        "CRYPTO_BASIS": 0.06,
    },
    desk_currency={"GLOBAL_MACRO": ["USD", "EUR", "GBP"], "EM_MACRO": ["MXN", "BRL", "ZAR"]},
    desk_fx_pairs={
        "FX_CARRY": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "NZD/USD"],
        "EM_MACRO": ["USD/MXN", "USD/BRL", "USD/ZAR", "USD/INR"],
    },
    book_indices={"VOL_ARB": ["SPX", "NDX", "SX5E"], "IDX_HEDGE": ["SPX", "SX5E", "NKY"]},
    book_equities={
        "US_TECH_LS": ["AAPL", "MSFT", "NVDA", "TSLA"],
        "US_CORE_LS": ["JPM", "XOM", "PFE", "BA"],
        "EU_LS": ["SAP", "ASML", "SIE", "TTE", "BNP", "NESN", "HSBA", "SHEL"],
    },
    book_cds={"IG_LS": "CDX.NA.IG", "HY_LS": "CDX.NA.HY"},
    book_commodities={
        "ENERGY_TREND": ["BRENT", "WTI", "NATGAS"],
        "METALS_TREND": ["GOLD", "SILVER", "COPPER"],
    },
    default_index_book="IDX_HEDGE",
    index_option_book="VOL_ARB",
    default_equity_book="US_CORE_LS",
    equity_option_book="US_TECH_LS",
    size_scale=0.25,
    injector="fund",
    preferred_counterparty="PB_GS",
    nav=2.0e9,
    # Breadth products sit in existing strategy books (no reserved books at the fund).
    recipe_books={
        "swaption": ["GM_USD", "GM_EUR_GBP"],
        "ir_future": ["GM_USD", "GM_EUR_GBP"],
        "cds_single_name": ["IG_LS", "HY_LS"],
        "commodity_option": ["ENERGY_TREND", "METALS_TREND"],
        "etf": ["US_CORE_LS", "EU_LS"],
        "equity_exotic": ["VOL_ARB"],
    },
    book_cds_names={
        "IG_LS": [e for e, v in ref.CDS_SINGLE_NAMES.items() if v[5] == "IG"],
        "HY_LS": [e for e, v in ref.CDS_SINGLE_NAMES.items() if v[5] == "HY"],
    },
    book_commodity_options={
        "ENERGY_TREND": ["BRENT", "WTI", "NATGAS"],
        "METALS_TREND": ["GOLD", "SILVER", "COPPER"],
    },
    book_funds={"US_CORE_LS": ["USTECH", "SPXTR", "GLBNRG"], "EU_LS": ["EUBLUE"]},
    book_exotics={"VOL_ARB": ["SPX", "NDX", "SX5E"]},
    reserve_books=False,
)


def build_multi_strategy_fund() -> Organisation:
    desks, books, traders = [], [], []
    n = 1
    for sid, bid, name, ac, region, book_defs in _STRATEGIES:
        desks.append(
            Desk(desk_id=sid, business_id=bid, name=name, asset_class=ac, region=region, head=f"PM {name}")
        )
        for book_id, le, bname in book_defs:
            books.append(Book(book_id=book_id, desk_id=sid, legal_entity_id=le, name=bname, strategy=sid))
        traders.append(Trader(trader_id=f"PM{n:02d}", desk_id=sid, name=f"Portfolio Manager {n:02d}"))
        n += 1
    return Organisation(
        firm=Firm(firm_id=FIRM_ID, name="Meridian Multi-Strategy Fund", firm_type="HEDGE_FUND"),
        legal_entities=(
            LegalEntity(
                legal_entity_id="MSF_MASTER",
                firm_id=FIRM_ID,
                name="Meridian Master Fund Ltd",
                jurisdiction="KY",
                functional_currency="USD",
            ),
            LegalEntity(
                legal_entity_id="MSF_FEEDER",
                firm_id=FIRM_ID,
                name="Meridian Onshore Feeder LP",
                jurisdiction="US",
                functional_currency="USD",
            ),
        ),
        businesses=tuple(Business(business_id=i, firm_id=FIRM_ID, name=n_) for i, n_ in _BUSINESSES),
        desks=tuple(desks),
        books=tuple(books),
        traders=tuple(traders),
    )


# id, name, type, country, rating, tight CSA
_PBS: list[tuple[str, str, str, str]] = [
    ("PB_GS", "Prime Broker GS", "US", "A+"),
    ("PB_MS", "Prime Broker MS", "US", "A"),
    ("PB_JPM", "Prime Broker JPM", "US", "AA-"),
    ("PB_BARC", "Prime Broker Barclays", "GB", "A"),
]
_OTHER = [
    ("CCP_LCH", "LCH Ltd", CounterpartyType.CCP, "GB", "AA"),
    ("CCP_CME", "CME Clearing", CounterpartyType.CCP, "US", "AA"),
    ("CCP_ICE", "ICE Clear", CounterpartyType.CCP, "US", "AA"),
    ("EXCH_NYSE", "NYSE", CounterpartyType.EXCHANGE, "US", "NR"),
    ("EXCH_XETRA", "Xetra", CounterpartyType.EXCHANGE, "DE", "NR"),
    ("EXCH_EUREX", "Eurex", CounterpartyType.EXCHANGE, "DE", "NR"),
    ("EXCH_CME", "CME Group", CounterpartyType.EXCHANGE, "US", "NR"),
    ("EXCH_ICE", "ICE Futures", CounterpartyType.EXCHANGE, "GB", "NR"),
    ("EXCH_COINBASE", "Coinbase", CounterpartyType.EXCHANGE, "US", "NR"),
    ("EXCH_DTC", "DTC / Treasury settlement", CounterpartyType.EXCHANGE, "US", "NR"),
]


def build_fund_counterparties(org: Organisation) -> CounterpartyUniverse:
    cps = [
        Counterparty(
            counterparty_id=i,
            name=n,
            counterparty_type=CounterpartyType.BROKER_DEALER,
            country=c,
            sector="Financials",
            rating=r,
            internal_pd=0.0008,
        )
        for i, n, c, r in _PBS
    ]
    cps += [
        Counterparty(
            counterparty_id=i,
            name=n,
            counterparty_type=t,
            country=c,
            sector="Financials",
            rating=r,
            internal_pd=0.0003 if t is CounterpartyType.CCP else None,
        )
        for i, n, t, c, r in _OTHER
    ]
    csas, sets = [], []
    for i, _, _, _ in _PBS:
        csa = CSA(
            csa_id=f"CSA_{i}",
            collateral_currency="USD",
            threshold_we_post=0.0,
            threshold_they_post=0.0,
            minimum_transfer_amount=250_000.0,
            independent_amount=0.0,
            rounding=10_000.0,
            haircut=0.0,
        )
        csas.append(csa)
        for le in org.legal_entities:
            sets.append(
                NettingSet(
                    netting_set_id=f"NS_{i}_{le.legal_entity_id}",
                    counterparty_id=i,
                    legal_entity_id=le.legal_entity_id,
                    agreement_type="PBA",
                    csa_id=csa.csa_id,
                )
            )
    return CounterpartyUniverse(cps, sets, csas)


def build_fund(nav: float = 2.0e9) -> Fund:
    investors = (
        Investor(
            investor_id="INV_PENSION_A",
            name="Northern Teachers Pension",
            investor_type="PENSION",
            share_of_nav=0.22,
            dealing=DealingFrequency.QUARTERLY,
            notice_days=90,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_SWF",
            name="Gulf Sovereign Wealth Fund",
            investor_type="SOVEREIGN_WEALTH",
            share_of_nav=0.18,
            dealing=DealingFrequency.QUARTERLY,
            notice_days=90,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_FOF_1",
            name="Atlas Fund of Funds",
            investor_type="FOF",
            share_of_nav=0.15,
            dealing=DealingFrequency.MONTHLY,
            notice_days=30,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_FOF_2",
            name="Meridian Access Platform",
            investor_type="FOF",
            share_of_nav=0.12,
            dealing=DealingFrequency.MONTHLY,
            notice_days=30,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_ENDOW",
            name="Riverside University Endowment",
            investor_type="ENDOWMENT",
            share_of_nav=0.10,
            dealing=DealingFrequency.QUARTERLY,
            notice_days=60,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_FAMILY",
            name="Lindqvist Family Office",
            investor_type="FAMILY_OFFICE",
            share_of_nav=0.08,
            dealing=DealingFrequency.MONTHLY,
            notice_days=30,
            gate_pct=0.25,
        ),
        Investor(
            investor_id="INV_PRINCIPALS",
            name="Partners and employees",
            investor_type="PRINCIPALS",
            share_of_nav=0.09,
            dealing=DealingFrequency.ANNUAL,
            notice_days=180,
            gate_pct=1.0,
            lockup_until=date(2028, 12, 31),
        ),
        Investor(
            investor_id="INV_OTHER",
            name="Other investors",
            investor_type="FOF",
            share_of_nav=0.06,
            dealing=DealingFrequency.MONTHLY,
            notice_days=45,
            gate_pct=0.25,
        ),
    )
    return Fund(
        firm_id=FIRM_ID,
        name="Meridian Multi-Strategy Fund",
        nav=nav,
        inception=date(2019, 4, 1),
        investors=investors,
        prime_brokers=tuple(i for i, _, _, _ in _PBS),
    )


@dataclass(frozen=True)
class FundWorld:
    organisation: Organisation
    counterparties: CounterpartyUniverse
    fund: Fund
