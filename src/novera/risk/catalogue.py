"""The measure catalogue: every risk measure the platform produces, grouped by risk area,
with its definition, unit, the dashboard screen that shows it and the methodology record
that governs it.

Reference data read from code. ``tests/test_api.py`` checks that every entry points at an
existing record in ``docs/methodology/`` whose title and version match, and that every
market, counterparty, regulatory, fund and data-quality record is catalogued.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MeasureSpec:
    methodology: str
    """Methodology record id, e.g. MR-002; doubles as the measure id."""
    name: str
    definition: str
    unit: str
    screen: str
    """Dashboard page where the measure is read."""
    title: str
    """Title of the methodology record, checked against the file."""
    version: str = "1.0.0"
    face: str = "bank and fund"


@dataclass(frozen=True)
class RiskArea:
    area: str
    name: str
    measures: tuple[MeasureSpec, ...]


MEASURE_CATALOGUE: tuple[RiskArea, ...] = (
    RiskArea(
        "MARKET",
        "Market risk",
        (
            MeasureSpec(
                "MR-001",
                "Sensitivities",
                "DV01, CS01, FX, equity and commodity delta, vega and gamma by bump-and-reprice "
                "against the shared risk-factor universe, so every measure keys off the same factors.",
                "reporting currency per unit shock",
                "Drill-down",
                "Sensitivities by bump-and-reprice",
            ),
            MeasureSpec(
                "MR-002",
                "VaR 99% 1-day",
                "Historical simulation over the stored history with full revaluation of every trade; "
                "the official VaR.",
                "reporting currency",
                "VaR",
                "Historical-simulation VaR, full revaluation",
            ),
            MeasureSpec(
                "MR-003",
                "Expected shortfall 97.5%",
                "Average loss beyond the 97.5% quantile of the same scenario set as the VaR.",
                "reporting currency",
                "VaR",
                "Expected shortfall",
            ),
            MeasureSpec(
                "MR-004",
                "Delta-gamma-vega VaR (challenger)",
                "VaR from a Taylor expansion on the sensitivities; reconciled daily to the full-revaluation "
                "figure to catch pricing and data problems.",
                "reporting currency",
                "VaR",
                "Delta-gamma-vega VaR (challenger)",
            ),
            MeasureSpec(
                "MR-010",
                "Monte Carlo VaR",
                "VaR from simulated factor moves on the delta-gamma-vega approximation, as a second "
                "challenger to the historical figure.",
                "reporting currency",
                "VaR",
                "Monte Carlo VaR (delta-gamma-vega)",
            ),
            MeasureSpec(
                "MR-011",
                "VaR backtesting",
                "Exceptions of realised P&L against VaR over the lookback, Kupiec and Christoffersen "
                "tests and the Basel traffic-light zone.",
                "exceptions, p-values, zone",
                "VaR",
                "VaR backtesting",
            ),
            MeasureSpec(
                "MR-005",
                "Stress testing",
                "Full revaluation under historical episodes and hypothetical shocks; worst scenario "
                "feeds the stress-loss limits.",
                "reporting currency",
                "Stress",
                "Stress testing",
                version="1.1.0",
            ),
            MeasureSpec(
                "MR-007",
                "Daily P&L explain",
                "Day-on-day P&L as a full-revaluation waterfall (carry, factor moves, new and dead "
                "trades) with a sensitivity-based challenger and the unexplained residual.",
                "reporting currency",
                "P&L explain",
                "Daily P&L explain",
            ),
            MeasureSpec(
                "MR-012",
                "Concentration",
                "Herfindahl index, effective number and top-N shares by dimension, plus curve-node "
                "concentration of DV01.",
                "shares and indices",
                "Concentration & liquidity",
                "Concentration",
            ),
            MeasureSpec(
                "MR-013",
                "Liquidity",
                "Days to liquidate each position against volume, bid-ask cost and a liquidity-adjusted VaR.",
                "days, reporting currency",
                "Concentration & liquidity",
                "Liquidity",
            ),
            MeasureSpec(
                "MR-014",
                "Fund look-through",
                "Exposure to constituents held through ETFs and mutual funds, direct versus via fund.",
                "reporting currency",
                "Concentration & liquidity",
                "Fund look-through",
            ),
        ),
    ),
    RiskArea(
        "COUNTERPARTY",
        "Counterparty risk",
        (
            MeasureSpec(
                "CR-001",
                "Exposure profile (EE, EPE, PFE)",
                "Expected and potential future exposure per netting set on simulated market paths "
                "with full revaluation at each time step.",
                "reporting currency",
                "Counterparty",
                "Exposure simulation",
                face="bank",
            ),
            MeasureSpec(
                "CR-002",
                "Collateral under the CSA",
                "Collateral calls from thresholds, minimum transfer amounts, independent amounts and "
                "the margin period of risk, netted into the exposure.",
                "reporting currency",
                "Counterparty",
                "Collateral under the CSA",
                face="bank",
            ),
            MeasureSpec(
                "CR-003",
                "CVA and DVA",
                "Credit valuation adjustments from the exposure profile, counterparty hazard rates and "
                "our own spread.",
                "reporting currency",
                "Counterparty",
                "CVA and DVA",
                face="bank",
            ),
            MeasureSpec(
                "CR-004",
                "Wrong-way risk",
                "Correlation between a counterparty's exposure and its credit proxy, flagged above "
                "a threshold.",
                "correlation, flag",
                "Counterparty",
                "Wrong-way risk indicator",
                face="bank",
            ),
        ),
    ),
    RiskArea(
        "REGULATORY",
        "Regulatory capital",
        (
            MeasureSpec(
                "REG-001",
                "FRTB standardised approach",
                "Sensitivities-based method by risk class plus the default risk charge.",
                "reporting currency",
                "Capital",
                "FRTB standardised approach",
                face="bank",
            ),
            MeasureSpec(
                "REG-002",
                "FRTB internal models approach",
                "Expected shortfall across liquidity horizons, non-modellable factors, the multiplier "
                "from backtesting and the P&L attribution test by desk.",
                "reporting currency",
                "Capital",
                "FRTB internal models approach",
                face="bank",
            ),
            MeasureSpec(
                "REG-003",
                "SA-CCR",
                "Exposure at default per netting set from replacement cost and add-ons, then RWA "
                "and capital.",
                "reporting currency",
                "Capital",
                "SA-CCR",
                face="bank",
            ),
            MeasureSpec(
                "REG-004",
                "SIMM-lite initial margin",
                "Initial margin per netting set from sensitivities with a simplified SIMM aggregation.",
                "reporting currency",
                "Capital",
                "SIMM-lite initial margin",
                face="bank",
            ),
            MeasureSpec(
                "REG-005",
                "BA-CVA capital",
                "Basic-approach CVA capital from EAD, maturity and sector risk weights.",
                "reporting currency",
                "Capital",
                "BA-CVA capital",
                face="bank",
            ),
            MeasureSpec(
                "REG-006",
                "Funding cash ladder",
                "Contractual cash flows by currency and time bucket, with the cumulative net position.",
                "reporting currency",
                "Capital",
                "Funding cash ladder",
                face="bank",
            ),
        ),
    ),
    RiskArea(
        "FUND",
        "Fund risk",
        (
            MeasureSpec(
                "HF-001",
                "Exposures and leverage",
                "Long, short, gross and net exposure by strategy and asset class as a share of NAV.",
                "share of NAV",
                "Fund",
                "Fund exposures and leverage",
                face="fund",
            ),
            MeasureSpec(
                "HF-002",
                "Prime-broker margin",
                "Margin by broker replicated from house schedules, with netting benefit and concentration.",
                "reporting currency, share",
                "Fund",
                "Prime-broker margin replication",
                face="fund",
            ),
            MeasureSpec(
                "HF-003",
                "Factor exposures",
                "P&L per one-sigma move in each market factor by strategy, from regression on the history.",
                "share of NAV per sigma",
                "Fund",
                "Factor exposures",
                face="fund",
            ),
            MeasureSpec(
                "HF-004",
                "Redemption stress",
                "Redemptions due per dealing date against what can be liquidated by then, with gates.",
                "reporting currency, coverage",
                "Fund",
                "Redemption stress",
                face="fund",
            ),
            MeasureSpec(
                "HF-005",
                "Strategy attribution",
                "Contribution of each strategy to VaR, P&L and hypothetical return and volatility.",
                "share, reporting currency",
                "Fund",
                "Strategy attribution",
                face="fund",
            ),
            MeasureSpec(
                "HF-006",
                "Crowding",
                "Crowded positions from a crowding score, days to exit and share of gross exposure.",
                "score, days",
                "Fund",
                "Crowding",
                face="fund",
            ),
        ),
    ),
    RiskArea(
        "CONTROL",
        "Control and governance",
        (
            MeasureSpec(
                "DQ-001",
                "Data-quality verdict",
                "Checks on trades, reference data and market data that answer 'can I trust today's run?' "
                "with a GREEN, AMBER or RED verdict; findings never block the run.",
                "findings, verdict",
                "Data quality",
                "Data-quality checks and run verdict",
            ),
            MeasureSpec(
                "MD-002",
                "Market-data proxies",
                "Missing or stale market data replaced before pricing from a proxy family, with an "
                "audit trail.",
                "actions",
                "Data quality",
                "Market-data proxies",
            ),
            MeasureSpec(
                "MR-006",
                "Limit utilisation",
                "Current measure against each approved limit with warning and breach status, "
                "temporary increases applied.",
                "utilisation",
                "Limit management",
                "Limit utilisation and breach status",
            ),
            MeasureSpec(
                "MR-008",
                "Breach workflow",
                "Raise, acknowledge, escalate and close breaches; request and decide temporary limit "
                "increases under the approval matrix.",
                "status",
                "Breaches",
                "Breach workflow and temporary limit increases",
            ),
            MeasureSpec(
                "MR-009",
                "Independent challenger",
                "Reconciliation of the platform's VaR against an independent vendor feed, with the gap "
                "attributed to scope, market data, pricing model and methodology.",
                "reporting currency",
                "Challenger",
                "Independent challenger reconciliation",
                face="bank",
            ),
        ),
    ),
)


def measure_reference() -> dict[str, Any]:
    return {
        "areas": [
            {
                "area": a.area,
                "name": a.name,
                "measures": [
                    {
                        "methodology": m.methodology,
                        "name": m.name,
                        "definition": m.definition,
                        "unit": m.unit,
                        "screen": m.screen,
                        "methodology_title": m.title,
                        "version": m.version,
                        "face": m.face,
                    }
                    for m in a.measures
                ],
            }
            for a in MEASURE_CATALOGUE
        ]
    }
