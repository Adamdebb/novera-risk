"""Exposure to central counterparties (decision round 39, records CR-006 and REG-007).

Trades cleared at an OTC clearing house (LCH, ICE) sit in one netting set per CCP and legal
entity, built here from the trades rather than stored: an agreement of type ``CCP`` with daily
variation margin at zero threshold and a five-day margin period. The exposure engine values them
with the bilateral sets in the same simulation; the margin we post at the CCP adds to the
exposure, because a house account is not bankruptcy-remote. Capital follows the qualifying-CCP
treatment of Basel CRE54, simplified: 2% risk weight on the trade exposure and a default-fund
charge ``K_CM = max(K_CCP × DF_i / (DF_CCP + DF_CM), 8% × 2% × DF_i)``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd

from novera.domain.counterparties import CSA, NettingSet
from novera.domain.enums import ClearingType
from novera.domain.organisation import Organisation
from novera.domain.trades import Trade

CCP_COUNTERPARTIES: tuple[str, ...] = ("CCP_LCH", "CCP_ICE")
CCP_AGREEMENT = "CCP"
CCP_MPOR_DAYS = 5
QCCP_TRADE_RW = 0.02
# Default fund per CCP (decision 39.4): our contribution, the CCP's prefunded fund (own plus every
# member's) and its hypothetical capital K_CCP from its public quantitative disclosures. Assumed
# levels in reporting currency, not quotes.
CCP_DEFAULT_FUND: dict[str, dict[str, float]] = {
    "CCP_LCH": {"contribution": 60e6, "total_fund": 6.0e9, "k_ccp": 1.5e9},
    "CCP_ICE": {"contribution": 25e6, "total_fund": 2.5e9, "k_ccp": 0.6e9},
}


def ccp_netting_set_id(ccp: str, legal_entity_id: str) -> str:
    return f"NS_{ccp}_{legal_entity_id}"


@dataclass
class CCPSets:
    """CCP netting sets, their margin terms and the cleared trades relabelled to them."""

    netting_sets: list[NettingSet] = field(default_factory=list)
    csas: list[CSA] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    @property
    def ids(self) -> set[str]:
        return {n.netting_set_id for n in self.netting_sets}

    @property
    def by_trade(self) -> dict[str, str]:
        return {t.trade_id: str(t.netting_set_id) for t in self.trades}


def build_ccp_sets(trades: Iterable[Trade], org: Organisation) -> CCPSets:
    """Group the live trades cleared at an OTC clearing house by CCP and legal entity."""
    out = CCPSets()
    seen: set[str] = set()
    for t in trades:
        if t.status.value != "LIVE" or t.clearing is not ClearingType.CLEARED:
            continue
        if t.counterparty_id not in CCP_COUNTERPARTIES:
            continue
        try:
            le = org.hierarchy_for_book(t.book_id)["legal_entity_id"]
        except KeyError:  # unknown book: a data-quality finding (TRADE_UNKNOWN_BOOK), not exposure
            continue
        ns_id = ccp_netting_set_id(t.counterparty_id, le)
        if ns_id not in seen:
            seen.add(ns_id)
            csa_id = f"CSA_{t.counterparty_id}"
            out.netting_sets.append(
                NettingSet(
                    netting_set_id=ns_id,
                    counterparty_id=t.counterparty_id,
                    legal_entity_id=le,
                    agreement_type=CCP_AGREEMENT,
                    csa_id=csa_id,
                )
            )
            if csa_id not in {c.csa_id for c in out.csas}:
                out.csas.append(
                    CSA(
                        csa_id=csa_id,
                        collateral_currency="USD",
                        threshold_we_post=0.0,
                        threshold_they_post=0.0,
                        minimum_transfer_amount=0.0,
                        margin_period_of_risk_days=CCP_MPOR_DAYS,
                    )
                )
        out.trades.append(t.model_copy(update={"netting_set_id": ns_id}))
    return out


def qccp_capital(ead_by_ccp: dict[str, float], posted_margin_by_ccp: dict[str, float]) -> pd.DataFrame:
    """Qualifying-CCP capital per CCP: trade exposure (SA-CCR EAD plus the unsegregated margin
    posted) at a 2% risk weight, and the default-fund charge from CCP_DEFAULT_FUND."""
    rows = []
    for ccp in sorted(set(ead_by_ccp) | set(posted_margin_by_ccp)):
        trade_exposure = ead_by_ccp.get(ccp, 0.0) + posted_margin_by_ccp.get(ccp, 0.0)
        df = CCP_DEFAULT_FUND.get(ccp, {"contribution": 0.0, "total_fund": 1.0, "k_ccp": 0.0})
        k_cm = max(
            df["k_ccp"] * df["contribution"] / max(df["total_fund"], 1.0),
            0.08 * QCCP_TRADE_RW * df["contribution"],
        )
        rows.append(
            {
                "counterparty_id": ccp,
                "trade_exposure": trade_exposure,
                "trade_exposure_capital": trade_exposure * QCCP_TRADE_RW * 0.08,
                "default_fund_contribution": df["contribution"],
                "default_fund_capital": k_cm,
                "capital": trade_exposure * QCCP_TRADE_RW * 0.08 + k_cm,
            }
        )
    return pd.DataFrame(rows)
