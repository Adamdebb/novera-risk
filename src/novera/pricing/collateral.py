"""Which cash collateral each trade's CSA posts, handed to the pricers (decision 35.2).

The netting set and CSA reference data stay the one source of truth: valuation builds a
``CollateralMap`` from them and gives each collateral-aware pricer its trade's collateral
currency, which picks the discount curve (``MarketSnapshot.collateral_curve``). A cleared or
listed trade, and a bilateral one without a CSA, discount on their own currency's OIS curve
(``None``); funding for the uncollateralised is left to an FVA (decision 35.4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from novera.domain.counterparties import CSA, NettingSet
from novera.domain.enums import ProductType
from novera.domain.trades import Trade

# Products whose pricer takes the collateral currency (decision 35.3).
COLLATERAL_AWARE: frozenset[ProductType] = frozenset(
    {
        ProductType.INTEREST_RATE_SWAP,
        ProductType.SWAPTION,
        ProductType.FX_FORWARD,
        ProductType.FX_OPTION,
        ProductType.CDS_INDEX,
        ProductType.CDS_SINGLE_NAME,
        ProductType.REPO,
    }
)


@dataclass(frozen=True)
class CollateralMap:
    """Netting set id to the currency its CSA posts (``None`` for a netting set without one)."""

    by_netting_set: Mapping[str, str | None] = field(default_factory=dict)

    @classmethod
    def from_reference(cls, netting_sets: Iterable[NettingSet], csas: Iterable[CSA]) -> CollateralMap:
        ccy = {c.csa_id: c.collateral_currency for c in csas}
        return cls({n.netting_set_id: ccy.get(n.csa_id) if n.csa_id else None for n in netting_sets})

    def for_trade(self, trade: Trade) -> str | None:
        if trade.netting_set_id is None:
            return None
        return self.by_netting_set.get(trade.netting_set_id)
