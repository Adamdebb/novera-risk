"""Basic approach CVA capital (reduced BA-CVA). Record REG-005.

K = 2.33 × sqrt((ρ Σ_c SCVA_c)² + (1 − ρ²) Σ_c SCVA_c²), SCVA_c = RW_c × M_c × EAD_c × DF_c / α,
with ρ = 0.5, DF = (1 − e^{−0.05 M}) / (0.05 M), risk weights by counterparty sector and rating.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from novera.domain.counterparties import Counterparty, CounterpartyType

MODEL_VERSION = "1.0.0"
RHO = 0.5
ALPHA = 1.4
RW = {  # (sector, investment grade?) -> weight
    ("sovereign", True): 0.005,
    ("sovereign", False): 0.02,
    ("financial", True): 0.05,
    ("financial", False): 0.12,
    ("corporate", True): 0.03,
    ("corporate", False): 0.10,
}


def _sector(cp: Counterparty) -> str:
    if cp.counterparty_type is CounterpartyType.SOVEREIGN:
        return "sovereign"
    if cp.counterparty_type in (
        CounterpartyType.BANK,
        CounterpartyType.BROKER_DEALER,
        CounterpartyType.HEDGE_FUND,
        CounterpartyType.ASSET_MANAGER,
    ):
        return "financial"
    return "corporate"


def ba_cva(
    ead_by_cp: pd.DataFrame, maturities: dict[str, float], counterparties: dict[str, Counterparty]
) -> tuple[float, pd.DataFrame]:
    rows = []
    for _, r in ead_by_cp.iterrows():
        cid = r["counterparty_id"]
        cp = counterparties.get(cid)
        if cp is None:
            continue
        ig = cp.rating.startswith(("AAA", "AA", "A", "BBB"))
        rw = RW[(_sector(cp), ig)]
        m = max(maturities.get(cid, 1.0), 10 / 250)
        df = (1 - np.exp(-0.05 * m)) / (0.05 * m)
        scva = rw * m * float(r["ead"]) * df / ALPHA
        rows.append(
            {
                "counterparty_id": cid,
                "sector": _sector(cp),
                "investment_grade": ig,
                "risk_weight": rw,
                "maturity": m,
                "ead": float(r["ead"]),
                "scva": scva,
            }
        )
    t = pd.DataFrame(rows)
    if t.empty:
        return 0.0, t
    k = 2.33 * np.sqrt((RHO * t["scva"].sum()) ** 2 + (1 - RHO**2) * (t["scva"] ** 2).sum())
    return float(k), t.sort_values("scva", ascending=False)
