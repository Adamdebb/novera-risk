"""Concentration measures. Methodology record MR-012.

Shares and Herfindahl–Hirschman indices (HHI, 0..1) of |exposure| across positions, risk
factors, desks, counterparties and issuers; top-N shares; tenor concentration per curve.
Exposure for HHI purposes is the absolute component VaR where available, else |PV|.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

MODEL_VERSION = "1.0.0"


def hhi(values: pd.Series) -> float:
    a = values.abs()
    tot = a.sum()
    return float(((a / tot) ** 2).sum()) if tot else 0.0


def top_share(values: pd.Series, n: int) -> float:
    a = values.abs().sort_values(ascending=False)
    tot = a.sum()
    return float(a.head(n).sum() / tot) if tot else 0.0


@dataclass
class ConcentrationReport:
    by_dimension: pd.DataFrame  # dimension, hhi, effective_number, top1_share, top5_share, top10_share, basis
    top_positions: pd.DataFrame  # trade_id, desk_id, product_type, pv, var_contribution, share_of_var
    tenor: pd.DataFrame  # currency, bucket, dv01, share_of_abs_dv01
    issuer: pd.DataFrame  # issuer/underlying, pv, share
    flags: list[str] = field(default_factory=list)


def concentration(
    valuation: pd.DataFrame,
    contributions: pd.DataFrame,
    sens: pd.DataFrame,
    top_share_flag: float = 0.25,
    hhi_flag: float = 0.25,
) -> ConcentrationReport:
    v = (
        valuation[valuation["status"] == "LIVE"]
        .merge(contributions[["trade_id", "var_contribution"]], on="trade_id", how="left")
        .fillna({"var_contribution": 0.0})
    )
    v["issuer"] = v["product_type"] + ":" + v["trade_id"].str.extract(r"^([A-Z]+)_")[0].fillna("")
    rows = []
    for dim, basis in (
        ("trade_id", "var"),
        ("desk_id", "var"),
        ("book_id", "var"),
        ("counterparty_id", "pv"),
        ("currency", "var"),
        ("asset_class", "var"),
    ):
        col = "var_contribution" if basis == "var" else "pv"
        g = v.groupby(dim, dropna=False)[col].sum()
        h = hhi(g)
        rows.append(
            {
                "dimension": dim,
                "basis": col,
                "groups": int(len(g)),
                "hhi": h,
                "effective_number": (1 / h) if h else 0.0,
                "top1_share": top_share(g, 1),
                "top5_share": top_share(g, 5),
                "top10_share": top_share(g, 10),
                "largest": str(g.abs().idxmax()) if len(g) else "",
            }
        )
    by_dim = pd.DataFrame(rows)
    # Risk factors: share of |sensitivity| by underlying within each measure family.
    for measure in ("DV01", "CS01", "EQ_DELTA", "FX_DELTA", "CMD_DELTA", "CRYPTO_DELTA", "VEGA"):
        sub = sens[sens["measure"] == measure]
        if sub.empty:
            continue
        g = sub.groupby("underlying")["value"].sum()
        h = hhi(g)
        by_dim.loc[len(by_dim)] = {
            "dimension": f"factor:{measure}",
            "basis": measure,
            "groups": int(len(g)),
            "hhi": h,
            "effective_number": (1 / h) if h else 0.0,
            "top1_share": top_share(g, 1),
            "top5_share": top_share(g, 5),
            "top10_share": top_share(g, 10),
            "largest": str(g.abs().idxmax()),
        }
    total_var = v["var_contribution"].sum()
    top = v.reindex(v["var_contribution"].abs().sort_values(ascending=False).index).head(20)
    top = top[
        ["trade_id", "desk_id", "book_id", "product_type", "counterparty_id", "pv", "var_contribution"]
    ].copy()
    top["share_of_var"] = top["var_contribution"] / total_var if total_var else 0.0
    dv = sens[sens["measure"] == "DV01"].groupby(["underlying", "bucket"])["value"].sum().reset_index()
    dv = dv.rename(columns={"underlying": "currency", "value": "dv01"})
    dv["share_of_abs_dv01"] = dv.groupby("currency")["dv01"].transform(lambda s: s.abs() / s.abs().sum())
    iss = v.groupby("issuer")["pv"].sum().abs().sort_values(ascending=False).reset_index()
    iss["share"] = iss["pv"] / iss["pv"].sum() if iss["pv"].sum() else 0.0
    flags = []
    for _, r in by_dim.iterrows():
        if r["dimension"] in ("trade_id",) and r["top1_share"] > top_share_flag:
            flags.append(f"Single trade {r['largest']} carries {r['top1_share']:.0%} of component VaR.")
        if r["dimension"].startswith("factor:") and r["hhi"] > hhi_flag and r["groups"] > 2:
            flags.append(
                f"{r['basis']} risk concentrated: HHI {r['hhi']:.2f}, largest {r['largest']} "
                f"{r['top1_share']:.0%}."
            )
    firm_abs_dv01 = dv["dv01"].abs().sum()
    material = dv.groupby("currency")["dv01"].apply(lambda s: s.abs().sum())
    for (ccy, bucket), s in dv.groupby(["currency", "bucket"])["share_of_abs_dv01"]:
        if float(s.iloc[0]) > 0.5 and firm_abs_dv01 and material.get(ccy, 0.0) >= 0.05 * firm_abs_dv01:
            flags.append(f"{ccy} curve risk: {float(s.iloc[0]):.0%} of |DV01| sits on the {bucket} node.")
    return ConcentrationReport(by_dim, top, dv, iss.head(20), flags)
