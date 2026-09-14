"""Which risk factors each trade depends on. Used to reprice only what a bump affects.

A prefix ending in ':' matches a factor family (all nodes of a curve or surface); any
other prefix is an exact factor id. Every non-reporting-currency trade also depends on the
FX factor that converts it, so FX bumps move the reporting-currency PV of foreign books.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from novera.domain.enums import ProductType
from novera.domain.trades import Trade


def _fx_prefixes(ccy: str, reporting: str) -> list[str]:
    if ccy == reporting:
        return []
    # The snapshot may hold either quoting direction; and crosses go through USD.
    out = [f"FX:{ccy}{reporting}", f"FX:{reporting}{ccy}"]
    if reporting != "USD":
        out += [f"FX:{ccy}USD", f"FX:USD{ccy}", f"FX:{reporting}USD", f"FX:USD{reporting}"]
    return out


def _pair_prefixes(pair: str) -> list[str]:
    base, quote = pair[:3], pair[4:]
    out = [f"FX:{base}{quote}", f"FX:{quote}{base}"]
    for c in (base, quote):
        if c != "USD":
            out += [f"FX:{c}USD", f"FX:USD{c}"]
    return out


def factor_prefixes(trade: Trade, reporting_currency: str) -> tuple[str, ...]:
    ins = trade.instrument
    pt = trade.product_type
    ccy = trade.currency
    out: list[str] = _fx_prefixes(ccy, reporting_currency)
    if pt in (ProductType.GOVERNMENT_BOND, ProductType.INTEREST_RATE_SWAP):
        out.append(f"IR:{ccy}:")
    elif pt is ProductType.FX_SPOT:
        out += _pair_prefixes(ins.pair)  # type: ignore[attr-defined]
    elif pt is ProductType.FX_FORWARD:
        out += _pair_prefixes(ins.pair) + [f"IR:{ins.base_currency}:", f"IR:{ins.quote_currency}:"]  # type: ignore[attr-defined]
    elif pt is ProductType.FX_OPTION:
        key = ins.pair.replace("/", "")  # type: ignore[attr-defined]
        out += _pair_prefixes(ins.pair) + [
            f"IR:{ins.base_currency}:",
            f"IR:{ins.quote_currency}:",
            f"VOL:{key}:",
        ]  # type: ignore[attr-defined]
    elif pt is ProductType.CASH_EQUITY:
        out.append(f"EQ:{ins.ticker}")  # type: ignore[attr-defined]
    elif pt is ProductType.EQUITY_INDEX_FUTURE:
        out += [f"EQIDX:{ins.index}", f"IR:{ccy}:"]  # type: ignore[attr-defined]
    elif pt is ProductType.EQUITY_OPTION:
        u = ins.underlying  # type: ignore[attr-defined]
        out += [f"EQ:{u}", f"EQIDX:{u}", f"IR:{ccy}:", f"VOL:{u}:"]
    elif pt is ProductType.COMMODITY_FUTURE:
        out.append(f"CMD:{ins.commodity}:")  # type: ignore[attr-defined]
    elif pt is ProductType.CDS_INDEX:
        out += [f"CDS:{ins.index_family}", f"IR:{ccy}:"]  # type: ignore[attr-defined]
    elif pt is ProductType.CRYPTO_SPOT:
        out.append(f"CRYPTO:{ins.symbol}")  # type: ignore[attr-defined]
    return tuple(dict.fromkeys(out))


def matches(prefix: str, factor_id: str) -> bool:
    return factor_id.startswith(prefix) if prefix.endswith(":") else factor_id == prefix


@dataclass
class DependencyIndex:
    """Trade ids by factor prefix, so a bumped factor finds its trades in one lookup."""

    reporting_currency: str
    by_prefix: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    prefixes_by_trade: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, trades: Iterable[Trade], reporting_currency: str) -> DependencyIndex:
        idx = cls(reporting_currency)
        for t in trades:
            p = factor_prefixes(t, reporting_currency)
            idx.prefixes_by_trade[t.trade_id] = p
            for prefix in p:
                idx.by_prefix[prefix].add(t.trade_id)
        return idx

    def trades_for(self, factor_ids: Iterable[str]) -> set[str]:
        out: set[str] = set()
        fids = list(factor_ids)
        for prefix, tids in self.by_prefix.items():
            if any(matches(prefix, f) for f in fids):
                out |= tids
        return out

    def depends_on(self, trade_id: str, factor_id: str) -> bool:
        return any(matches(p, factor_id) for p in self.prefixes_by_trade.get(trade_id, ()))
