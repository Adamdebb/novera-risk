"""Data-quality checks that answer: can I trust today's run? (docs/04-governance.md)

Each check yields findings with a severity. The verdict is RED if any finding is
CRITICAL, AMBER if any is MAJOR or MINOR, GREEN otherwise. The run always completes; the
verdict and the affected trade ids travel with the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from novera.domain.enums import ClearingType, TradeStatus, Venue
from novera.domain.organisation import Organisation
from novera.domain.trades import Trade
from novera.market_data.risk_factors import RiskFactor
from novera.market_data.snapshot import MarketSnapshot
from novera.risk.factor_mapping import DependencyIndex

SEVERITIES = ("CRITICAL", "MAJOR", "MINOR", "INFO")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    subject: str  # factor id, trade id, or scope
    affected_trade_ids: tuple[str, ...] = ()
    owner: str = "Market Data"


@dataclass
class DataQualityReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        sev = {f.severity for f in self.findings}
        if "CRITICAL" in sev:
            return "RED"
        if sev & {"MAJOR", "MINOR"}:
            return "AMBER"
        return "GREEN"

    @property
    def affected_trade_ids(self) -> set[str]:
        return {tid for f in self.findings for tid in f.affected_trade_ids}

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "subject": f.subject,
                    "message": f.message,
                    "affected_trades": len(f.affected_trade_ids),
                    "owner": f.owner,
                }
                for f in self.findings
            ],
            columns=["code", "severity", "subject", "message", "affected_trades", "owner"],
        )


def check_market_data(
    market: MarketSnapshot, universe: list[RiskFactor], index: DependencyIndex, max_stale_days: int = 0
) -> list[Finding]:
    out: list[Finding] = []
    expected = {f.factor_id for f in universe}
    missing = sorted(expected - set(market.values))
    for fid in missing:
        affected = tuple(sorted(index.trades_for([fid])))
        out.append(
            Finding(
                "MD_MISSING_FACTOR",
                "MAJOR" if affected else "MINOR",
                f"{fid} absent from the market snapshot; dependent trades priced by interpolation",
                fid,
                affected,
            )
        )
    stale_groups: dict[str, list[str]] = {}
    for fid in market.stale_factors():
        age = (market.as_of - market.observation_date(fid)).days
        if age > max_stale_days:
            key = ":".join(fid.split(":")[:2]) + ":" if fid.count(":") >= 2 else fid
            stale_groups.setdefault(key, []).append(fid)
    for key, fids in stale_groups.items():
        affected = tuple(sorted(index.trades_for(fids)))
        obs = market.observation_date(fids[0])
        out.append(
            Finding(
                "MD_STALE_FACTOR",
                "MAJOR" if affected else "MINOR",
                f"{len(fids)} nodes of {key} last observed {obs}; "
                f"{len(affected)} trades priced off stale data",
                key,
                affected,
            )
        )
    return out


def proxy_findings(proxies, index: DependencyIndex) -> list[Finding]:
    """One INFO finding per proxied family (MD-002), listing the trades priced off the proxy.
    Families kept stale without a usable proxy are reported as MINOR so they stay visible."""
    out: list[Finding] = []
    groups: dict[tuple[str, str, str], list] = {}
    for a in proxies.actions:
        key = ":".join(a.factor_id.split(":")[:2]) + ":" if a.factor_id.count(":") >= 2 else a.factor_id
        groups.setdefault((key, a.kind, a.source), []).append(a)
    for (key, kind, source), acts in groups.items():
        affected = tuple(sorted(index.trades_for([a.factor_id for a in acts])))
        if kind == "KEPT_STALE":
            out.append(
                Finding(
                    "MD_PROXY_UNAVAILABLE",
                    "MINOR",
                    f"{len(acts)} stale nodes of {key} kept as observed: {acts[0].reason}",
                    key,
                    affected,
                )
            )
            continue
        verb = {"INTERPOLATED": "interpolated", "ROLLED": "rolled from", "RELEVELLED": "re-levelled with"}[
            kind
        ]
        out.append(
            Finding(
                "MD_PROXY_APPLIED",
                "INFO",
                f"{len(acts)} nodes of {key} {verb} {source}; {len(affected)} trades priced off the proxy",
                key,
                affected,
            )
        )
    return out


def check_trades(
    trades: list[Trade], org: Organisation, counterparty_ids: set[str], netting_set_ids: set[str], as_of: date
) -> list[Finding]:
    out: list[Finding] = []
    books = {b.book_id for b in org.books}
    for t in trades:
        if t.status is TradeStatus.INVALID:
            out.append(
                Finding(
                    "TRADE_INVALID",
                    "MAJOR",
                    "; ".join(t.validation_errors) or "marked invalid",
                    t.trade_id,
                    (t.trade_id,),
                    owner="Trade Control",
                )
            )
        if t.book_id not in books:
            out.append(
                Finding(
                    "TRADE_UNKNOWN_BOOK",
                    "MAJOR",
                    f"book {t.book_id} not in the organisation",
                    t.trade_id,
                    (t.trade_id,),
                    owner="Trade Control",
                )
            )
        if t.counterparty_id not in counterparty_ids:
            out.append(
                Finding(
                    "TRADE_UNKNOWN_COUNTERPARTY",
                    "MAJOR",
                    f"counterparty {t.counterparty_id} not in reference data",
                    t.trade_id,
                    (t.trade_id,),
                    owner="Counterparty Data",
                )
            )
        if (
            t.venue is Venue.OTC
            and t.clearing is ClearingType.BILATERAL
            and t.netting_set_id
            and t.netting_set_id not in netting_set_ids
        ):
            out.append(
                Finding(
                    "TRADE_UNKNOWN_NETTING_SET",
                    "MAJOR",
                    f"netting set {t.netting_set_id} not in reference data",
                    t.trade_id,
                    (t.trade_id,),
                    owner="Counterparty Data",
                )
            )
    return out


def check_valuation(valuation: pd.DataFrame) -> list[Finding]:
    out: list[Finding] = []
    failed = valuation[valuation["error"].notna()]
    if len(failed):
        out.append(
            Finding(
                "VAL_UNPRICED",
                "CRITICAL" if len(failed) > 0.02 * len(valuation) else "MAJOR",
                f"{len(failed)} trades failed to price",
                "valuation",
                tuple(failed["trade_id"]),
                owner="Risk IT",
            )
        )
    dead = valuation[valuation["note"].isin(["matured", "expired", "settled"])]
    if len(dead):
        out.append(
            Finding(
                "VAL_DEAD_TRADES",
                "MINOR",
                f"{len(dead)} matured, expired or settled trades still in the feed",
                "valuation",
                tuple(dead["trade_id"]),
                owner="Trade Control",
            )
        )
    return out


def check_pnl_residuals(
    residuals: pd.DataFrame, abs_tolerance: float, rel_tolerance: float = 0.25
) -> list[Finding]:
    """Trades whose Greeks-based P&L prediction misses the full-revaluation P&L by more than
    tolerance. Expected on options and on big-move days; flagged for investigation."""
    if residuals.empty:
        return []
    r = residuals
    bad = r[(r["residual"].abs() > abs_tolerance) & (r["residual"].abs() > rel_tolerance * r["actual"].abs())]
    if bad.empty:
        return []
    return [
        Finding(
            "PNL_UNEXPLAINED",
            "MINOR",
            f"{len(bad)} trades with P&L not explained by sensitivities beyond tolerance",
            "pnl_attribution",
            tuple(bad["trade_id"]),
            owner="Market Risk",
        )
    ]
