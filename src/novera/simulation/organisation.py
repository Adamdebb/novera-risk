"""Template organisation: Global Macro Bank.

Deterministic (no randomness). Fourteen desks across six asset classes, three legal
entities, roughly thirty books. Counterparties include banks, dealers, funds, corporates,
sovereigns, CCPs and exchanges, with netting sets and CSAs for bilateral OTC business.
Two counterparties are deliberately uncollateralised to seed wrong-way-risk stories.
"""

from __future__ import annotations

from dataclasses import dataclass

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

FIRM_ID = "GMB"

# desk_id, business_id, name, asset class, region, books (book_id, legal_entity_id, name)
_DESKS: list[tuple[str, str, str, AssetClass, str, list[tuple[str, str, str]]]] = [
    (
        "USD_RATES",
        "MACRO",
        "USD Rates",
        AssetClass.RATES,
        "AMER",
        [
            ("USD_MACRO_RV", "GMB_NY", "USD Macro RV"),
            ("USD_SWAPS_FLOW", "GMB_NY", "USD Swaps Flow"),
            ("UST_CASH", "GMB_NY", "UST Cash"),
        ],
    ),
    (
        "EUR_RATES",
        "MACRO",
        "EUR Rates",
        AssetClass.RATES,
        "EMEA",
        [
            ("EUR_SWAPS", "GMB_LN", "EUR Swaps"),
            ("EGB_CASH", "GMB_LN", "European Govies"),
        ],
    ),
    (
        "GBP_RATES",
        "MACRO",
        "GBP Rates",
        AssetClass.RATES,
        "EMEA",
        [
            ("GBP_SWAPS", "GMB_LN", "GBP Swaps"),
            ("GILTS", "GMB_LN", "Gilts"),
        ],
    ),
    (
        "JPY_RATES",
        "MACRO",
        "JPY Rates",
        AssetClass.RATES,
        "APAC",
        [
            ("JPY_SWAPS", "GMB_SG", "JPY Swaps"),
            ("JGB_CASH", "GMB_SG", "JGB Cash"),
        ],
    ),
    (
        "EM_RATES",
        "MACRO",
        "EM Rates",
        AssetClass.RATES,
        "EMEA",
        [
            ("EM_LOCAL_RATES", "GMB_LN", "EM Local Rates"),
        ],
    ),
    (
        "G10_FX",
        "MACRO",
        "G10 FX",
        AssetClass.FX,
        "EMEA",
        [
            ("G10_SPOT_FWD", "GMB_LN", "G10 Spot and Forwards"),
            ("G10_FX_OPTIONS", "GMB_LN", "G10 FX Options"),
        ],
    ),
    (
        "EM_FX",
        "MACRO",
        "EM FX",
        AssetClass.FX,
        "APAC",
        [
            ("EM_FX_FWD", "GMB_SG", "EM FX Forwards"),
            ("EM_FX_OPTIONS", "GMB_SG", "EM FX Options"),
        ],
    ),
    (
        "INDEX_EQ",
        "EQUITIES",
        "Index Equity",
        AssetClass.EQUITY,
        "EMEA",
        [
            ("EU_INDEX", "GMB_LN", "European Index"),
            ("US_INDEX", "GMB_NY", "US Index"),
            ("INDEX_VOL", "GMB_LN", "Index Volatility"),
        ],
    ),
    (
        "SINGLE_NAME_EQ",
        "EQUITIES",
        "Single Name Equity",
        AssetClass.EQUITY,
        "AMER",
        [
            ("US_CASH_EQ", "GMB_NY", "US Cash Equity"),
            ("EU_CASH_EQ", "GMB_LN", "European Cash Equity"),
            ("SN_OPTIONS", "GMB_NY", "Single Name Options"),
        ],
    ),
    (
        "IG_CREDIT",
        "CREDIT",
        "IG Credit",
        AssetClass.CREDIT,
        "AMER",
        [
            ("CDX_IG", "GMB_NY", "CDX IG Index"),
            ("ITRAXX_MAIN", "GMB_LN", "iTraxx Main"),
        ],
    ),
    (
        "HY_CREDIT",
        "CREDIT",
        "HY Credit",
        AssetClass.CREDIT,
        "EMEA",
        [
            ("CDX_HY", "GMB_NY", "CDX HY Index"),
            ("ITRAXX_XOVER", "GMB_LN", "iTraxx Crossover"),
        ],
    ),
    (
        "ENERGY",
        "COMMODITIES",
        "Energy",
        AssetClass.COMMODITY,
        "EMEA",
        [
            ("CRUDE", "GMB_LN", "Crude Oil"),
            ("NATGAS", "GMB_LN", "Natural Gas"),
        ],
    ),
    (
        "METALS",
        "COMMODITIES",
        "Metals",
        AssetClass.COMMODITY,
        "AMER",
        [
            ("PRECIOUS", "GMB_NY", "Precious Metals"),
            ("BASE_METALS", "GMB_LN", "Base Metals"),
        ],
    ),
    (
        "DIGITAL",
        "DIGITAL_ASSETS",
        "Digital Assets",
        AssetClass.DIGITAL_ASSET,
        "APAC",
        [
            ("CRYPTO_SPOT", "GMB_SG", "Crypto Spot"),
        ],
    ),
]

_BUSINESSES = [
    ("MACRO", "Macro"),
    ("EQUITIES", "Equities"),
    ("CREDIT", "Credit"),
    ("COMMODITIES", "Commodities"),
    ("DIGITAL_ASSETS", "Digital Assets"),
]

_LEGAL_ENTITIES = [
    ("GMB_NY", "GMB Securities (New York)", "US", "USD"),
    ("GMB_LN", "GMB International (London)", "GB", "GBP"),
    ("GMB_SG", "GMB Asia (Singapore)", "SG", "SGD"),
]


def build_global_macro_bank() -> Organisation:
    desks, books, traders = [], [], []
    n = 1
    for desk_id, business_id, name, ac, region, book_defs in _DESKS:
        desks.append(
            Desk(
                desk_id=desk_id,
                business_id=business_id,
                name=name,
                asset_class=ac,
                region=region,
                head=f"Head of {name}",
            )
        )
        for book_id, le, bname in book_defs:
            books.append(Book(book_id=book_id, desk_id=desk_id, legal_entity_id=le, name=bname))
        for _ in range(2):
            traders.append(Trader(trader_id=f"T{n:02d}", desk_id=desk_id, name=f"Trader {n:02d}"))
            n += 1
    return Organisation(
        firm=Firm(firm_id=FIRM_ID, name="Global Macro Bank", firm_type="INVESTMENT_BANK"),
        legal_entities=tuple(
            LegalEntity(legal_entity_id=i, firm_id=FIRM_ID, name=n_, jurisdiction=j, functional_currency=c)
            for i, n_, j, c in _LEGAL_ENTITIES
        ),
        businesses=tuple(Business(business_id=i, firm_id=FIRM_ID, name=n_) for i, n_ in _BUSINESSES),
        desks=tuple(desks),
        books=tuple(books),
        traders=tuple(traders),
    )


# --- Counterparties ------------------------------------------------------------------


@dataclass(frozen=True)
class CounterpartyUniverse:
    counterparties: list[Counterparty]
    netting_sets: list[NettingSet]
    csas: list[CSA]

    @property
    def bilateral(self) -> list[Counterparty]:
        return [
            c
            for c in self.counterparties
            if c.counterparty_type not in (CounterpartyType.CCP, CounterpartyType.EXCHANGE)
        ]

    def ccp(self, name: str) -> Counterparty:
        return next(c for c in self.counterparties if c.counterparty_id == name)

    def netting_set_for(self, counterparty_id: str, legal_entity_id: str) -> NettingSet | None:
        for ns in self.netting_sets:
            if ns.counterparty_id == counterparty_id and ns.legal_entity_id == legal_entity_id:
                return ns
        return None


# id, name, type, country, sector, rating, collateralised
_COUNTERPARTIES: list[tuple[str, str, CounterpartyType, str, str, str, bool]] = [
    ("BANK_A", "Bank A", CounterpartyType.BANK, "US", "Financials", "A+", True),
    ("BANK_B", "Bank B", CounterpartyType.BANK, "GB", "Financials", "A", True),
    ("BANK_C", "Bank C", CounterpartyType.BANK, "DE", "Financials", "A-", True),
    ("BANK_D", "Bank D", CounterpartyType.BANK, "JP", "Financials", "A", True),
    ("BANK_E", "Bank E", CounterpartyType.BANK, "FR", "Financials", "A+", True),
    ("BANK_F", "Bank F", CounterpartyType.BANK, "CH", "Financials", "AA-", True),
    ("DEALER_G", "Dealer G", CounterpartyType.BROKER_DEALER, "US", "Financials", "BBB+", True),
    ("DEALER_H", "Dealer H", CounterpartyType.BROKER_DEALER, "GB", "Financials", "BBB", True),
    ("HF_ALPHA", "Alpha Capital", CounterpartyType.HEDGE_FUND, "US", "Financials", "NR", True),
    ("HF_BETA", "Beta Partners", CounterpartyType.HEDGE_FUND, "KY", "Financials", "NR", True),
    ("HF_GAMMA", "Gamma Macro", CounterpartyType.HEDGE_FUND, "GB", "Financials", "NR", True),
    ("AM_ONE", "Asset Manager One", CounterpartyType.ASSET_MANAGER, "US", "Financials", "AA", True),
    ("AM_TWO", "Asset Manager Two", CounterpartyType.ASSET_MANAGER, "NL", "Financials", "A+", True),
    ("CORP_ENERGY", "Northsea Energy plc", CounterpartyType.CORPORATE, "GB", "Energy", "BBB", False),
    ("CORP_AIR", "TransAtlantic Air", CounterpartyType.CORPORATE, "US", "Industrials", "BB+", False),
    ("CORP_AUTO", "Rhein Motors AG", CounterpartyType.CORPORATE, "DE", "Consumer", "BBB+", True),
    ("SOV_EM", "Republic of Andoria", CounterpartyType.SOVEREIGN, "AR", "Sovereign", "B", False),
    ("SOV_DM", "Kingdom of Nordland", CounterpartyType.SOVEREIGN, "NO", "Sovereign", "AAA", True),
    ("INS_ONE", "Continental Insurance", CounterpartyType.ASSET_MANAGER, "DE", "Insurance", "AA-", True),
    ("PENSION_ONE", "National Pension Fund", CounterpartyType.ASSET_MANAGER, "CA", "Pension", "AAA", True),
    ("CCP_LCH", "LCH Ltd", CounterpartyType.CCP, "GB", "Financials", "AA", False),
    ("CCP_CME", "CME Clearing", CounterpartyType.CCP, "US", "Financials", "AA", False),
    ("CCP_ICE", "ICE Clear", CounterpartyType.CCP, "US", "Financials", "AA", False),
    ("EXCH_NYSE", "NYSE", CounterpartyType.EXCHANGE, "US", "Financials", "NR", False),
    ("EXCH_XETRA", "Xetra", CounterpartyType.EXCHANGE, "DE", "Financials", "NR", False),
    ("EXCH_EUREX", "Eurex", CounterpartyType.EXCHANGE, "DE", "Financials", "NR", False),
    ("EXCH_CME", "CME Group", CounterpartyType.EXCHANGE, "US", "Financials", "NR", False),
    ("EXCH_ICE", "ICE Futures", CounterpartyType.EXCHANGE, "GB", "Financials", "NR", False),
    ("EXCH_COINBASE", "Coinbase", CounterpartyType.EXCHANGE, "US", "Financials", "NR", False),
    ("EXCH_DTC", "DTC / Treasury settlement", CounterpartyType.EXCHANGE, "US", "Financials", "NR", False),
]

_PD_BY_RATING = {
    "AAA": 0.0002,
    "AA": 0.0003,
    "AA-": 0.0004,
    "A+": 0.0006,
    "A": 0.0008,
    "A-": 0.001,
    "BBB+": 0.0015,
    "BBB": 0.002,
    "BB+": 0.006,
    "B": 0.04,
    "NR": 0.01,
}


def build_counterparty_universe(org: Organisation) -> CounterpartyUniverse:
    cpties = [
        Counterparty(
            counterparty_id=i,
            name=n,
            counterparty_type=t,
            country=c,
            sector=s,
            rating=r,
            internal_pd=_PD_BY_RATING[r],
            on_watchlist=(i in {"CORP_AIR", "SOV_EM"}),
        )
        for i, n, t, c, s, r, _ in _COUNTERPARTIES
    ]
    csas: list[CSA] = []
    netting_sets: list[NettingSet] = []
    for i, _, t, _, _, r, collateralised in _COUNTERPARTIES:
        if t in (CounterpartyType.CCP, CounterpartyType.EXCHANGE):
            continue
        csa_id: str | None = None
        if collateralised:
            # Banks and dealers on tight terms; funds and corporates on looser thresholds.
            tight = t in (CounterpartyType.BANK, CounterpartyType.BROKER_DEALER)
            csa = CSA(
                csa_id=f"CSA_{i}",
                collateral_currency="USD",
                threshold_we_post=0.0 if tight else 5_000_000.0,
                threshold_they_post=0.0 if tight else (2_000_000.0 if r.startswith("A") else 10_000_000.0),
                minimum_transfer_amount=250_000.0 if tight else 500_000.0,
                independent_amount=0.0 if tight else 1_000_000.0,
                rounding=10_000.0,
                haircut=0.0 if tight else 0.02,
            )
            csas.append(csa)
            csa_id = csa.csa_id
        for le in org.legal_entities:
            netting_sets.append(
                NettingSet(
                    netting_set_id=f"NS_{i}_{le.legal_entity_id}",
                    counterparty_id=i,
                    legal_entity_id=le.legal_entity_id,
                    agreement_type="ISDA",
                    csa_id=csa_id,
                )
            )
    return CounterpartyUniverse(cpties, netting_sets, csas)
