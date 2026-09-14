"""Seeded limit hierarchy for Global Macro Bank.

Calibrated once by hand against the risk run of 2026-09-11 (seed 42) so that the planted
problems breach and a few other limits sit in warning. Amounts are USD unless the limit is
a CONCENTRATION share.
"""

from __future__ import annotations

from datetime import date

from novera.domain import CounterpartyType, HierarchyLevel, Limit, LimitScope, LimitType, Organisation
from novera.simulation.organisation import CounterpartyUniverse

EFFECTIVE = date(2026, 1, 1)


def _lim(
    lid: str,
    lt: LimitType,
    level: HierarchyLevel,
    entity: str,
    amount: float,
    owner: str,
    rationale: str = "",
    warning: float = 0.8,
    **filters,
) -> Limit:
    return Limit(
        limit_id=lid,
        limit_type=lt,
        scope=LimitScope(level=level, entity_id=entity, **filters),
        amount=amount,
        warning_threshold=warning,
        owner=owner,
        approver="CRO",
        effective_from=EFFECTIVE,
        rationale=rationale,
    )


_DESK_VAR: dict[str, float] = {
    "USD_RATES": 45e6,
    "EUR_RATES": 15e6,
    "GBP_RATES": 15e6,
    "JPY_RATES": 5e6,
    "EM_RATES": 5e6,
    "G10_FX": 15e6,
    "EM_FX": 12e6,
    "INDEX_EQ": 45e6,
    "SINGLE_NAME_EQ": 25e6,
    "IG_CREDIT": 8e6,
    "HY_CREDIT": 8e6,
    "ENERGY": 35e6,
    "METALS": 20e6,
    "DIGITAL": 15e6,
}
_DESK_STRESS: dict[str, float] = {
    "USD_RATES": 350e6,
    "EUR_RATES": 60e6,
    "GBP_RATES": 60e6,
    "JPY_RATES": 20e6,
    "EM_RATES": 15e6,
    "G10_FX": 40e6,
    "EM_FX": 50e6,
    "INDEX_EQ": 180e6,
    "SINGLE_NAME_EQ": 120e6,
    "IG_CREDIT": 30e6,
    "HY_CREDIT": 30e6,
    "ENERGY": 250e6,
    "METALS": 60e6,
    "DIGITAL": 50e6,
}
_BUSINESS_VAR: dict[str, float] = {
    "MACRO": 60e6,
    "EQUITIES": 65e6,
    "CREDIT": 10e6,
    "COMMODITIES": 35e6,
    "DIGITAL_ASSETS": 15e6,
}
_CPTY_LIMIT: dict[CounterpartyType, float] = {
    CounterpartyType.BANK: 150e6,
    CounterpartyType.BROKER_DEALER: 75e6,
    CounterpartyType.HEDGE_FUND: 100e6,
    CounterpartyType.ASSET_MANAGER: 100e6,
    CounterpartyType.CORPORATE: 25e6,
    CounterpartyType.SOVEREIGN: 50e6,
}


def build_limits(
    org: Organisation, cp: CounterpartyUniverse, business_date: date | None = None
) -> list[Limit]:
    firm = org.firm.firm_id
    heads = {d.desk_id: d.head or f"Head of {d.name}" for d in org.desks}
    out: list[Limit] = [
        _lim("FIRM_VAR", LimitType.VAR, HierarchyLevel.FIRM, firm, 90e6, "CRO", "Board risk appetite"),
        _lim("FIRM_ES", LimitType.EXPECTED_SHORTFALL, HierarchyLevel.FIRM, firm, 100e6, "CRO"),
        _lim(
            "FIRM_STRESS",
            LimitType.STRESS_LOSS,
            HierarchyLevel.FIRM,
            firm,
            600e6,
            "CRO",
            "Worst case across the stress library",
        ),
    ]
    for b, amt in _BUSINESS_VAR.items():
        name = org.business(b).name
        out.append(_lim(f"{b}_VAR", LimitType.VAR, HierarchyLevel.BUSINESS, b, amt, f"Head of {name}"))
    for d, amt in _DESK_VAR.items():
        out.append(_lim(f"{d}_VAR", LimitType.VAR, HierarchyLevel.DESK, d, amt, heads[d]))
        out.append(
            _lim(f"{d}_STRESS", LimitType.STRESS_LOSS, HierarchyLevel.DESK, d, _DESK_STRESS[d], heads[d])
        )

    # Rates: parallel DV01 per currency, a 10Y bucket limit and a curve-concentration limit on USD.
    dv01 = {
        "USD_RATES": ("USD", 3.0e6),
        "EUR_RATES": ("EUR", 600e3),
        "GBP_RATES": ("GBP", 400e3),
        "JPY_RATES": ("JPY", 100e3),
    }
    for d, (ccy, amt) in dv01.items():
        out.append(
            _lim(f"{d}_DV01_{ccy}", LimitType.DV01, HierarchyLevel.DESK, d, amt, heads[d], currency=ccy)
        )
    for ccy in ("MXN", "BRL", "ZAR"):
        out.append(
            _lim(
                f"EM_RATES_DV01_{ccy}",
                LimitType.DV01,
                HierarchyLevel.DESK,
                "EM_RATES",
                50e3,
                heads["EM_RATES"],
                currency=ccy,
            )
        )
    out.append(
        _lim(
            "USD_RATES_DV01_USD_10Y",
            LimitType.DV01,
            HierarchyLevel.DESK,
            "USD_RATES",
            1.1e6,
            heads["USD_RATES"],
            "Bucket limit on the 10Y node",
            currency="USD",
            tenor_bucket="10Y",
        )
    )
    out.append(
        _lim(
            "USD_RATES_CONC_10Y",
            LimitType.CONCENTRATION,
            HierarchyLevel.DESK,
            "USD_RATES",
            0.40,
            heads["USD_RATES"],
            "No more than 40% of USD curve risk on one node",
            currency="USD",
            tenor_bucket="10Y",
        )
    )

    # Credit.
    out.append(
        _lim("IG_CREDIT_CS01", LimitType.CS01, HierarchyLevel.DESK, "IG_CREDIT", 400e3, heads["IG_CREDIT"])
    )
    out.append(
        _lim("HY_CREDIT_CS01", LimitType.CS01, HierarchyLevel.DESK, "HY_CREDIT", 100e3, heads["HY_CREDIT"])
    )

    # Equity, FX, vega.
    out.append(
        _lim(
            "INDEX_EQ_DELTA", LimitType.EQUITY_DELTA, HierarchyLevel.DESK, "INDEX_EQ", 6e6, heads["INDEX_EQ"]
        )
    )
    out.append(
        _lim(
            "SINGLE_NAME_EQ_DELTA",
            LimitType.EQUITY_DELTA,
            HierarchyLevel.DESK,
            "SINGLE_NAME_EQ",
            6e6,
            heads["SINGLE_NAME_EQ"],
        )
    )
    out.append(_lim("G10_FX_DELTA", LimitType.FX_DELTA, HierarchyLevel.DESK, "G10_FX", 6e6, heads["G10_FX"]))
    out.append(_lim("EM_FX_DELTA", LimitType.FX_DELTA, HierarchyLevel.DESK, "EM_FX", 5e6, heads["EM_FX"]))
    out.append(_lim("G10_FX_VEGA", LimitType.VEGA, HierarchyLevel.DESK, "G10_FX", 150e3, heads["G10_FX"]))
    out.append(_lim("EM_FX_VEGA", LimitType.VEGA, HierarchyLevel.DESK, "EM_FX", 50e3, heads["EM_FX"]))
    out.append(
        _lim("INDEX_EQ_VEGA", LimitType.VEGA, HierarchyLevel.DESK, "INDEX_EQ", 100e3, heads["INDEX_EQ"])
    )
    out.append(
        _lim(
            "SINGLE_NAME_EQ_VEGA",
            LimitType.VEGA,
            HierarchyLevel.DESK,
            "SINGLE_NAME_EQ",
            100e3,
            heads["SINGLE_NAME_EQ"],
        )
    )

    # Commodities: net delta per desk and Brent concentration within Energy.
    out.append(
        _lim(
            "ENERGY_CMD_DELTA", LimitType.COMMODITY_DELTA, HierarchyLevel.DESK, "ENERGY", 4e6, heads["ENERGY"]
        )
    )
    out.append(
        _lim(
            "METALS_CMD_DELTA", LimitType.COMMODITY_DELTA, HierarchyLevel.DESK, "METALS", 3e6, heads["METALS"]
        )
    )
    out.append(
        _lim(
            "ENERGY_CONC_BRENT",
            LimitType.CONCENTRATION,
            HierarchyLevel.DESK,
            "ENERGY",
            0.45,
            heads["ENERGY"],
            "No single energy contract above 45% of desk delta",
            risk_factor="BRENT",
        )
    )

    # Digital assets.
    out.append(
        _lim(
            "DIGITAL_BTC_DELTA",
            LimitType.COMMODITY_DELTA,
            HierarchyLevel.DESK,
            "DIGITAL",
            1.5e6,
            heads["DIGITAL"],
            risk_factor="BTC",
        )
    )

    # Counterparty exposure, pre-collateral, by counterparty type and rating.
    for c in cp.bilateral:
        amt = _CPTY_LIMIT.get(c.counterparty_type, 50e6)
        if c.rating in ("AAA", "AA", "AA-"):
            amt *= 1.5
        elif c.rating.startswith("B") and not c.rating.startswith("BBB"):
            amt *= 0.6
        if c.counterparty_id == "BANK_A":
            amt = 200e6
        out.append(
            _lim(
                f"CPTY_{c.counterparty_id}",
                LimitType.COUNTERPARTY_EXPOSURE,
                HierarchyLevel.COUNTERPARTY,
                c.counterparty_id,
                amt,
                "Head of Counterparty Risk",
                f"{c.counterparty_type.value} rated {c.rating}",
            )
        )
    return out
