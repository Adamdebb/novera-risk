"""Proxies for missing and stale market data, with an audit trail. Methodology record MD-002.

The raw snapshot is never changed. ``apply_proxies`` returns a new snapshot for pricing plus
one ``ProxyAction`` per factor it touched, so a run can store what was proxied, from what,
and why. Rules, in order:

1. Missing curve or surface nodes (IR, CMD, VOL, SWVOL families) are interpolated linearly
   in tenor from the neighbouring nodes of the same family, flat beyond the ends.
2. Missing single factors (FX, EQ, EQIDX, CDS, CRYPTO) are carried from the previous
   snapshot when one is available.
3. Stale families (observed before the business date) are re-levelled by the move of a
   proxy family of the same type since the stale observation date: for vol surfaces, the
   mean relative move of the proxy surface; for curves, the absolute move node by node.
   The proxy is the first non-stale family in the preference list, else the first non-stale
   family of the same asset class. Without a usable proxy the stale values are kept as is
   and the action says so.

Every action becomes an INFO finding (``MD_PROXY_APPLIED``); the MAJOR findings on the raw
snapshot are kept, so the verdict still reflects the data problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from novera.market_data.risk_factors import TENOR_YEARS, RiskFactor
from novera.market_data.snapshot import MarketSnapshot

MODEL_VERSION = "1.0.0"

CURVE_PREFIXES = ("IR:", "CMD:", "VOL:", "SWVOL:")
# Preferred proxy family per stale family (same type, most correlated in the simulated market).
PROXY_PREFERENCE: dict[str, list[str]] = {
    "VOL:EURUSD:": ["VOL:GBPUSD:", "VOL:USDCHF:", "VOL:AUDUSD:"],
    "VOL:GBPUSD:": ["VOL:EURUSD:", "VOL:AUDUSD:"],
    "VOL:USDJPY:": ["VOL:USDCHF:", "VOL:EURUSD:"],
    "VOL:SPX:": ["VOL:NDX:", "VOL:SX5E:"],
    "VOL:NDX:": ["VOL:SPX:"],
    "VOL:SX5E:": ["VOL:DAX:", "VOL:SPX:"],
    "VOL:DAX:": ["VOL:SX5E:"],
    "VOL:BRENT:": ["VOL:WTI:"],
    "VOL:WTI:": ["VOL:BRENT:"],
    "IR:EUR:": ["IR:GBP:", "IR:USD:"],
    "IR:GBP:": ["IR:EUR:", "IR:USD:"],
    "IR:USD:": ["IR:GBP:", "IR:EUR:"],
    "SWVOL:EUR:": ["SWVOL:GBP:", "SWVOL:USD:"],
    "SWVOL:GBP:": ["SWVOL:EUR:", "SWVOL:USD:"],
    "SWVOL:USD:": ["SWVOL:GBP:", "SWVOL:EUR:"],
}
COLUMNS = ["factor_id", "kind", "source", "original", "value", "reason"]


@dataclass(frozen=True)
class ProxyAction:
    factor_id: str
    kind: str  # INTERPOLATED, ROLLED, RELEVELLED, KEPT_STALE
    source: str
    original: float | None
    value: float
    reason: str


@dataclass
class ProxyResult:
    raw: MarketSnapshot
    market: MarketSnapshot
    actions: list[ProxyAction]

    @property
    def applied(self) -> bool:
        return any(a.kind != "KEPT_STALE" for a in self.actions)

    def table(self) -> pd.DataFrame:
        return pd.DataFrame([a.__dict__ for a in self.actions], columns=COLUMNS)


def family_of(factor_id: str) -> str:
    parts = factor_id.split(":")
    return parts[0] + ":" + parts[1] + ":" if len(parts) >= 3 else factor_id


def _tenor_years(factor_id: str) -> float | None:
    parts = factor_id.split(":")
    if factor_id.startswith(("IR:", "CMD:")) and len(parts) == 3:
        return TENOR_YEARS.get(parts[2])
    if factor_id.startswith(("VOL:", "SWVOL:")) and len(parts) == 4:
        return TENOR_YEARS.get(parts[2])  # expiry axis
    return None


def _interpolate_missing(
    market: MarketSnapshot, missing: list[str], values: dict[str, float]
) -> list[ProxyAction]:
    out: list[ProxyAction] = []
    for fid in missing:
        if not fid.startswith(CURVE_PREFIXES):
            continue
        parts = fid.split(":")
        t = _tenor_years(fid)
        if t is None:
            continue
        # Siblings share every id part except the tenor / expiry axis.
        fam = family_of(fid)
        siblings = []
        for k in market.factors_with_prefix(fam):
            kp = k.split(":")
            if len(kp) != len(parts) or (len(parts) == 4 and kp[3] != parts[3]):
                continue
            tt = _tenor_years(k)
            if tt is not None:
                siblings.append((tt, market.values[k], k))
        if not siblings:
            continue
        siblings.sort()
        xs = np.array([s[0] for s in siblings])
        ys = np.array([s[1] for s in siblings])
        v = float(np.interp(t, xs, ys))
        lo = max((s for s in siblings if s[0] <= t), default=siblings[0], key=lambda s: s[0])
        hi = min((s for s in siblings if s[0] >= t), default=siblings[-1], key=lambda s: s[0])
        values[fid] = v
        out.append(
            ProxyAction(
                fid,
                "INTERPOLATED",
                f"{lo[2]},{hi[2]}" if lo[2] != hi[2] else lo[2],
                None,
                v,
                "node missing from the snapshot; linear in tenor between neighbouring nodes",
            )
        )
    return out


def _roll_missing(
    missing: list[str], previous: MarketSnapshot | None, values: dict[str, float]
) -> list[ProxyAction]:
    out: list[ProxyAction] = []
    if previous is None:
        return out
    for fid in missing:
        if fid in values or fid.startswith(CURVE_PREFIXES) or fid not in previous.values:
            continue
        v = previous.values[fid]
        values[fid] = v
        out.append(
            ProxyAction(
                fid,
                "ROLLED",
                f"snapshot {previous.as_of}",
                None,
                v,
                "factor missing from the snapshot; carried from the previous business day",
            )
        )
    return out


def _proxy_family(stale_fam: str, market: MarketSnapshot, stale_fams: set[str]) -> str | None:
    for cand in PROXY_PREFERENCE.get(stale_fam, []):
        if cand not in stale_fams and market.factors_with_prefix(cand):
            return cand
    kind = stale_fam.split(":")[0] + ":"
    for k in market.factors_with_prefix(kind):
        fam = family_of(k)
        if fam != stale_fam and fam not in stale_fams:
            return fam
    return None


def _relevel_stale(
    market: MarketSnapshot,
    previous: MarketSnapshot | None,
    values: dict[str, float],
    observed: dict[str, date],
    max_stale_days: int,
) -> list[ProxyAction]:
    out: list[ProxyAction] = []
    stale = [
        f for f in market.stale_factors() if (market.as_of - market.observation_date(f)).days > max_stale_days
    ]
    fams: dict[str, list[str]] = {}
    for f in stale:
        fams.setdefault(family_of(f), []).append(f)
    stale_fams = set(fams)
    for fam, fids in fams.items():
        obs = market.observation_date(fids[0])
        proxy = _proxy_family(fam, market, stale_fams) if fam.endswith(":") else None
        # The proxy family's own level on the stale observation date: from the previous
        # snapshot when it is dated there, otherwise unavailable.
        ref = previous if (previous is not None and previous.as_of == obs) else None
        if proxy is None or ref is None:
            for f in fids:
                out.append(
                    ProxyAction(
                        f,
                        "KEPT_STALE",
                        "",
                        market.values[f],
                        market.values[f],
                        f"last observed {obs}; no proxy family with a reference on that date",
                    )
                )
            continue
        pfids = [k for k in market.factors_with_prefix(proxy) if k in ref.values]
        if not pfids:
            continue
        relative = fam.startswith(("VOL:", "SWVOL:", "CMD:"))
        if relative:
            move = float(np.mean([market.values[k] / ref.values[k] - 1.0 for k in pfids]))
        else:
            move = float(np.mean([market.values[k] - ref.values[k] for k in pfids]))
        for f in fids:
            old = market.values[f]
            new = old * (1.0 + move) if relative else old + move
            values[f] = new
            observed.pop(f, None)
            out.append(
                ProxyAction(
                    f,
                    "RELEVELLED",
                    proxy,
                    old,
                    new,
                    f"last observed {obs}; moved with {proxy} ({move:+.2%} relative)"
                    if relative
                    else f"last observed {obs}; moved with {proxy} ({move * 1e4:+.1f}bp)",
                )
            )
    return out


def apply_proxies(
    market: MarketSnapshot,
    universe: list[RiskFactor],
    previous: MarketSnapshot | None = None,
    max_stale_days: int = 0,
) -> ProxyResult:
    """Proxied snapshot for pricing plus the audit trail. ``market`` is returned untouched as
    ``raw``; the proxied snapshot keeps the same business date and source."""
    expected = [f.factor_id for f in universe]
    missing = sorted(set(expected) - set(market.values))
    values = dict(market.values)
    observed = dict(market.observed_at)
    actions: list[ProxyAction] = []
    actions += _interpolate_missing(market, missing, values)
    actions += _roll_missing(missing, previous, values)
    actions += _relevel_stale(market, previous, values, observed, max_stale_days)
    if not actions:
        return ProxyResult(market, market, [])
    proxied = MarketSnapshot(as_of=market.as_of, values=values, observed_at=observed, source=market.source)
    return ProxyResult(market, proxied, actions)


def apply_stored_proxies(market: MarketSnapshot, actions: pd.DataFrame) -> MarketSnapshot:
    """Rebuild the priced snapshot of a stored run from its ``md_proxies`` frame."""
    if actions is None or actions.empty:
        return market
    applied = actions[actions["kind"] != "KEPT_STALE"]
    if applied.empty:
        return market
    values = dict(market.values)
    observed = dict(market.observed_at)
    for _, r in applied.iterrows():
        values[str(r["factor_id"])] = float(r["value"])
        observed.pop(str(r["factor_id"]), None)
    return MarketSnapshot(as_of=market.as_of, values=values, observed_at=observed, source=market.source)
