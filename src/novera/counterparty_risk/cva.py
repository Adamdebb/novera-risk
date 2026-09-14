"""CVA, DVA and wrong-way-risk indicators. Records CR-003 and CR-004.

CVA = LGD × Σ_i EE(t_i) × DF(t_i) × (Q(t_{i-1}) − Q(t_i)) with a flat hazard rate from the
counterparty's one-year PD. DVA is the mirror on the negative exposure with the firm's own
spread. Wrong-way risk: correlation across paths, at the one-year point, between the
netting set's exposure and a credit-deterioration proxy for the counterparty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from novera.counterparty_risk.exposure import ExposureResult
from novera.domain.counterparties import Counterparty, CounterpartyType
from novera.market_data.curves import ZeroCurve

MODEL_VERSION = "1.0.0"


def hazard_from_pd(pd_1y: float) -> float:
    return -math.log(max(1.0 - min(pd_1y, 0.999), 1e-9))


def hazard_from_spread(spread_bp: float, recovery: float) -> float:
    return (spread_bp / 1e4) / max(1.0 - recovery, 1e-6)


@dataclass(frozen=True)
class CreditTerms:
    hazard: float
    lgd: float


def cva_from_profile(years: np.ndarray, ee: np.ndarray, df: np.ndarray, terms: CreditTerms) -> float:
    q = np.exp(-terms.hazard * years)
    q_prev = np.concatenate([[1.0], q[:-1]])
    return float(terms.lgd * np.sum(ee * df * (q_prev - q)))


def cva_table(
    profiles: pd.DataFrame,
    counterparties: dict[str, Counterparty],
    curve: ZeroCurve,
    own: CreditTerms,
    lgd: float,
) -> pd.DataFrame:
    """Per netting set: CVA, DVA, bilateral CVA (CVA − DVA)."""
    rows = []
    for k, grp in profiles.sort_values("years").groupby("netting_set_id"):
        cp = counterparties.get(grp["counterparty_id"].iloc[0])
        pd_1y = cp.internal_pd if cp and cp.internal_pd is not None else 0.01
        terms = CreditTerms(hazard_from_pd(pd_1y), lgd)
        years = grp["years"].to_numpy()
        df = curve.df(years)
        cva = cva_from_profile(years, grp["ee"].to_numpy(), df, terms)
        dva = cva_from_profile(years, grp["ene"].to_numpy(), df, own)
        cva_gross = cva_from_profile(years, grp["ee_gross"].to_numpy(), df, terms)
        rows.append(
            {
                "netting_set_id": k,
                "counterparty_id": grp["counterparty_id"].iloc[0],
                "pd_1y": pd_1y,
                "hazard": terms.hazard,
                "cva": cva,
                "dva": dva,
                "bcva": cva - dva,
                "cva_gross": cva_gross,
            }
        )
    return pd.DataFrame(rows)


# Credit-deterioration proxies: the factor whose rise signals the counterparty weakening.
def credit_proxy(cp: Counterparty) -> tuple[str, float] | None:
    """(factor_id, sign) where sign +1 means a rise in the factor is deterioration."""
    if cp.counterparty_type is CounterpartyType.SOVEREIGN:
        by_country = {
            "AR": "FX:USDARS",
            "TR": "FX:USDTRY",
            "BR": "FX:USDBRL",
            "MX": "FX:USDMXN",
            "ZA": "FX:USDZAR",
            "NO": None,
        }
        f = by_country.get(cp.country)
        return (f, 1.0) if f else None
    if cp.counterparty_type is CounterpartyType.CORPORATE:
        return (
            ("CDS:CDX.NA.HY" if cp.country == "US" else "CDS:ITRAXX.EUR.XOVER", 1.0)
            if not cp.rating.startswith("A")
            else ("CDS:CDX.NA.IG", 1.0)
        )
    if cp.counterparty_type in (CounterpartyType.BANK, CounterpartyType.BROKER_DEALER):
        return (
            "CDS:ITRAXX.EUR.MAIN" if cp.country in ("GB", "DE", "FR", "CH", "NL") else "CDS:CDX.NA.IG",
            1.0,
        )
    if cp.counterparty_type in (CounterpartyType.HEDGE_FUND, CounterpartyType.ASSET_MANAGER):
        return ("EQIDX:SPX", -1.0)  # equity down = fund weaker
    return None


def wrong_way_indicators(
    res: ExposureResult,
    proxy_paths: dict[str, np.ndarray],
    counterparties: dict[str, Counterparty],
    step_indices: list[int],
    flag_threshold: float = 0.5,
) -> pd.DataFrame:
    """Per netting set: the strongest correlation across paths, over the given grid steps,
    between gross exposure and the proxy move (signed so that positive = wrong way). Steps
    where the set carries no exposure are skipped."""
    rows = []
    for k, v in res.netting_values.items():
        ns = res.netting_sets[k]
        cp = counterparties.get(ns.counterparty_id)
        proxy = credit_proxy(cp) if cp else None
        if proxy is None or proxy[0] not in proxy_paths:
            rows.append(
                {
                    "netting_set_id": k,
                    "counterparty_id": ns.counterparty_id,
                    "proxy": None,
                    "correlation": None,
                    "at_step": None,
                    "wrong_way": False,
                }
            )
            continue
        fid, sign = proxy
        best, best_step = None, None
        for i in step_indices:
            e = np.maximum(v[i], 0.0)
            x = sign * proxy_paths[fid][i]
            if e.std() <= 0 or x.std() <= 0 or e.mean() <= 0:
                continue
            c = float(np.corrcoef(e, x)[0, 1])
            if best is None or c > best:
                best, best_step = c, res.grid[i][0]
        rows.append(
            {
                "netting_set_id": k,
                "counterparty_id": ns.counterparty_id,
                "proxy": fid,
                "correlation": best,
                "at_step": best_step,
                "wrong_way": best is not None and best > flag_threshold,
            }
        )
    return pd.DataFrame(rows)
