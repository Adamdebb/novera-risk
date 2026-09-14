"""Seeded limit set for the multi-strategy fund, in NAV terms."""

from __future__ import annotations

from datetime import date

from novera.domain import HierarchyLevel, Limit, LimitScope, LimitType, Organisation
from novera.simulation.fund import Fund
from novera.simulation.organisation import CounterpartyUniverse

EFFECTIVE = date(2026, 1, 1)


def _lim(lid, lt, level, entity, amount, owner, rationale="", warning=0.8, **filters) -> Limit:
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


def build_fund_limits(org: Organisation, cp: CounterpartyUniverse, fund: Fund) -> list[Limit]:
    nav = fund.nav
    firm = org.firm.firm_id
    out = [
        _lim(
            "FUND_VAR", LimitType.VAR, HierarchyLevel.FIRM, firm, 0.03 * nav, "CRO", "VaR 99% 1d at 3% of NAV"
        ),
        _lim("FUND_ES", LimitType.EXPECTED_SHORTFALL, HierarchyLevel.FIRM, firm, 0.04 * nav, "CRO"),
        _lim(
            "FUND_STRESS",
            LimitType.STRESS_LOSS,
            HierarchyLevel.FIRM,
            firm,
            0.15 * nav,
            "CRO",
            "Worst stress loss at 15% of NAV",
        ),
        _lim(
            "FUND_GROSS_LEVERAGE",
            LimitType.LEVERAGE,
            HierarchyLevel.FIRM,
            firm,
            fund.max_gross_leverage,
            "CRO",
            "Gross exposure over NAV",
        ),
        _lim(
            "FUND_MARGIN_USAGE",
            LimitType.MARGIN_USAGE,
            HierarchyLevel.FIRM,
            firm,
            0.50,
            "CFO",
            "Prime-broker margin over NAV",
        ),
        _lim(
            "FUND_PB_CONCENTRATION",
            LimitType.PB_CONCENTRATION,
            HierarchyLevel.FIRM,
            firm,
            0.45,
            "CFO",
            "Largest prime broker's share of margin",
        ),
    ]
    for d in org.desks:
        out.append(
            _lim(f"{d.desk_id}_VAR", LimitType.VAR, HierarchyLevel.DESK, d.desk_id, 0.012 * nav, d.head)
        )
        out.append(
            _lim(
                f"{d.desk_id}_STRESS",
                LimitType.STRESS_LOSS,
                HierarchyLevel.DESK,
                d.desk_id,
                0.06 * nav,
                d.head,
            )
        )
    for d in ("EQ_LS_US", "EQ_LS_EU", "INDEX_VOL_ARB"):
        out.append(
            _lim(
                f"{d}_EQ_DELTA",
                LimitType.EQUITY_DELTA,
                HierarchyLevel.DESK,
                d,
                0.005 * nav,
                f"PM {d}",
                "Net equity delta per 1% at 0.5% of NAV",
            )
        )
    out.append(
        _lim(
            "INDEX_VOL_ARB_VEGA",
            LimitType.VEGA,
            HierarchyLevel.DESK,
            "INDEX_VOL_ARB",
            0.0015 * nav,
            "PM INDEX_VOL_ARB",
        )
    )
    out.append(
        _lim(
            "EQ_LS_US_CONC_NVDA",
            LimitType.CONCENTRATION,
            HierarchyLevel.DESK,
            "EQ_LS_US",
            0.35,
            "PM EQ_LS_US",
            "No single name above 35% of the strategy's equity delta",
            risk_factor="NVDA",
        )
    )
    out.append(
        _lim(
            "GLOBAL_MACRO_DV01_USD",
            LimitType.DV01,
            HierarchyLevel.DESK,
            "GLOBAL_MACRO",
            0.0002 * nav,
            "PM GLOBAL_MACRO",
            currency="USD",
        )
    )
    out.append(
        _lim("CREDIT_LS_CS01", LimitType.CS01, HierarchyLevel.DESK, "CREDIT_LS", 0.0001 * nav, "PM CREDIT_LS")
    )
    for c in cp.bilateral:
        out.append(
            _lim(
                f"CPTY_{c.counterparty_id}",
                LimitType.COUNTERPARTY_EXPOSURE,
                HierarchyLevel.COUNTERPARTY,
                c.counterparty_id,
                0.05 * nav,
                "CFO",
                f"Prime broker exposure at 5% of NAV ({c.rating})",
            )
        )
    return out
