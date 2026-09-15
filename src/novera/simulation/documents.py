"""Simulated legal documents for the ingestion agent (AI-003): a CSA term sheet in the shape
banks actually circulate (Paragraph 13 elections), for a counterparty of the simulated bank."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from novera.domain.organisation import Organisation


def csa_term_sheet(
    org: Organisation,
    counterparty_name: str,
    legal_entity_id: str,
    currency: str = "USD",
    threshold_we_post: float = 10e6,
    threshold_they_post: float = 5e6,
    mta: float = 1e6,
    independent_amount: float = 2e6,
    rounding: float = 100e3,
    haircut_pct: float = 2.0,
    mpor_days: int = 10,
    as_of: date | None = None,
) -> str:
    le = next(x for x in org.legal_entities if x.legal_entity_id == legal_entity_id)
    d = as_of or date.today()
    return f"""CREDIT SUPPORT ANNEX
to the Schedule to the ISDA Master Agreement dated {d:%d %B %Y}

Party A: {le.name}
Party B: {counterparty_name}
Governing agreement: ISDA 2002 Master Agreement (English law CSA, title transfer)

Paragraph 11 elections and variables

(a) Base Currency and Eligible Currency
    Base currency: {currency}
    Eligible currency: {currency}, EUR

(b) Credit Support Obligations
    Threshold (Party A): {currency} {threshold_we_post / 1e6:,.1f} million
    Threshold (Party B): {currency} {threshold_they_post / 1e6:,.1f} million
    Minimum Transfer Amount: {currency} {mta / 1e6:,.1f} million
    Independent Amount (Party B): {currency} {independent_amount / 1e6:,.1f} million
    Rounding amount: {currency} {rounding:,.0f}

(c) Valuation and Timing
    Valuation Agent: Party A
    Valuation Date: daily, each Local Business Day
    Frequency of margin calls: daily
    Margin period of risk: {mpor_days} business days

(d) Eligible Credit Support and Valuation Percentage
    Cash in an Eligible Currency: 100%
    Government securities with residual maturity under 10 years: haircut: {haircut_pct:.1f}%

(e) Dispute Resolution
    Resolution Time: 13:00 London time on the Local Business Day after notice.

Signed for and on behalf of {le.name} and {counterparty_name}.
"""


def write_demo_term_sheet(
    org: Organisation, out_dir: Path, counterparty_name: str, legal_entity_id: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = counterparty_name.replace(" ", "_").replace(".", "")
    path = out_dir / f"CSA_{safe}_{legal_entity_id}.txt"
    path.write_text(csa_term_sheet(org, counterparty_name, legal_entity_id))
    return path
