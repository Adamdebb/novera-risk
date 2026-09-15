"""Market-data source catalogue: for every stored risk factor, where its history comes from
today and where real data could come from. Section "Source catalogue" of record MD-001.

Three statuses, decided from the store and the adapter maps, never by hand:

- ``REAL``       a provenance row exists: ``novera fetch`` merged real observations for it.
- ``AVAILABLE``  an adapter maps the factor (FRED, Yahoo, Coinbase) but nothing was fetched.
- ``SYNTHETIC``  no adapter maps it; the simulator (SIM-001) is its only source.

The free and paid candidates per factor group are reference text maintained here so the
dashboard, the API and the record read the same catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from novera.market_data.adapters.coinbase import PRODUCTS as COINBASE_PRODUCTS
from novera.market_data.adapters.fred import SERIES as FRED_SERIES
from novera.market_data.adapters.yahoo import SYMBOLS as YAHOO_SYMBOLS
from novera.market_data.risk_factors import RiskFactor

RECORD = "MD-001"
SIMULATOR = "simulator (SIM-001)"
STATUS_REAL, STATUS_AVAILABLE, STATUS_SYNTHETIC, STATUS_PARTIAL = "REAL", "AVAILABLE", "SYNTHETIC", "PARTIAL"
STATUS_LABELS = {
    STATUS_REAL: "Real, fetched",
    STATUS_AVAILABLE: "Real source wired, not fetched",
    STATUS_SYNTHETIC: "Synthetic",
    STATUS_PARTIAL: "Partly real or fetchable",
}


def adapter_coverage() -> dict[str, str]:
    """factor_id -> adapter name, read from the adapters' own maps so the page cannot drift."""
    out: dict[str, str] = {fid: "fred" for fid in FRED_SERIES.values()}
    out["IR:USD:15Y"] = "fred"  # interpolated by the adapter from 10Y and 20Y
    out.update({fid: "yahoo" for fid in YAHOO_SYMBOLS.values()})
    out.update({fid: "coinbase" for fid in COINBASE_PRODUCTS.values()})
    return out


@dataclass(frozen=True)
class SourceSpec:
    group: str
    free: str
    paid: str
    notes: str = ""


PAID_RATES = "Bloomberg (BVAL, swap curve pages), LSEG Refinitiv, ICE Data Services, Tradeweb closes"
PAID_VOL = "Bloomberg OVDV, LSEG Refinitiv, TP ICAP broker marks"

_SPECS: dict[str, SourceSpec] = {
    "IR:USD": SourceSpec(
        "USD rates",
        "FRED: Treasury constant-maturity yields DGS1MO..DGS30 (wired), SOFR, ICE swap-rate "
        "series where still published",
        PAID_RATES,
        "Treasury par yields stand in for zero rates; no swap or OIS curve is built",
    ),
    "IR:EUR": SourceSpec(
        "EUR rates",
        "ECB Data Portal: euro-area AAA government zero curve and euro short-term rate",
        PAID_RATES + ", EMMI EURIBOR licence",
        "No free daily EUR swap curve; government curve only",
    ),
    "IR:GBP": SourceSpec(
        "GBP rates",
        "Bank of England: daily gilt nominal and OIS zero curves (spreadsheets), SONIA",
        PAID_RATES,
        "The Bank of England OIS curve is the closest free swap-like curve in the universe",
    ),
    "IR:JPY": SourceSpec("JPY rates", "Japan Ministry of Finance: daily JGB yields by maturity", PAID_RATES),
    "IR:CHF": SourceSpec("CHF rates", "SNB data portal: Confederation bond yields, SARON", PAID_RATES),
    "IR:AUD": SourceSpec(
        "AUD rates", "RBA statistical tables F1 and F2: cash rate, government bond yields", PAID_RATES
    ),
    "IR:CAD": SourceSpec("CAD rates", "Bank of Canada Valet API: benchmark bond yields, CORRA", PAID_RATES),
    "IR:NZD": SourceSpec(
        "NZD rates", "RBNZ table B2: wholesale interest rates, government bond yields", PAID_RATES
    ),
    "IR:SGD": SourceSpec("SGD rates", "MAS API: SORA, Singapore Government Securities yields", PAID_RATES),
    "IR:MXN": SourceSpec("MXN rates", "Banxico SIE API: TIIE, government bond yields", PAID_RATES),
    "IR:BRL": SourceSpec(
        "BRL rates", "Banco Central do Brasil SGS API: Selic; B3 DI futures settlements", PAID_RATES
    ),
    "IR:INR": SourceSpec(
        "INR rates", "RBI and FBIL benchmark rates (registration), CCIL government yields", PAID_RATES
    ),
    "IR:TRY": SourceSpec(
        "TRY rates", "CBRT EVDS data system: policy rate, government bond yields", PAID_RATES
    ),
    "IR:ZAR": SourceSpec("ZAR rates", "SARB statistical tables: government bond yields, JIBAR", PAID_RATES),
    "IR:ARS": SourceSpec(
        "ARS rates", "BCRA API: policy rate, BADLAR", PAID_RATES, "Thin market; curve beyond 1Y is notional"
    ),
    "FX": SourceSpec(
        "FX spot",
        "Yahoo Finance daily closes (wired), FRED H.10 Fed noon rates, ECB euro reference rates",
        "Bloomberg BFIX, WM/Refinitiv 4pm fixes (LSEG), EBS",
    ),
    "EQIDX": SourceSpec(
        "Equity indices",
        "Yahoo Finance daily closes (wired), Stooq",
        "Index licences (S&P DJI, STOXX, Deutsche Börse, FTSE Russell, JPX), Bloomberg, LSEG",
    ),
    "EQ": SourceSpec(
        "Single stocks",
        "Yahoo Finance daily closes (wired), Alpha Vantage and Tiingo free tiers, Stooq",
        "Bloomberg, LSEG, FactSet, Polygon.io, exchange feeds",
    ),
    "CMD:ENERGY": SourceSpec(
        "Energy curves",
        "Yahoo Finance front-month continuous (wired for 1M); individual contracts by month "
        "code for later tenors; EIA and FRED spot series (WTI, Brent, Henry Hub); CME and ICE "
        "daily settlements for today only",
        "CME DataMine and ICE settlement history, Bloomberg, LSEG, Platts",
        "Only the front month is wired; the term structure needs a contract roll",
    ),
    "CMD:METAL": SourceSpec(
        "Metal curves",
        "Yahoo Finance front-month continuous (wired for 1M for gold, silver, copper; ALI=F "
        "for aluminium); CME daily settlements for today only; LBMA prices via FRED where kept",
        "LME data licence, CME DataMine, Bloomberg, LSEG",
        "Aluminium trades on the LME, which does not publish free daily history",
    ),
    "CDS:INDEX": SourceSpec(
        "CDS indices",
        "None with daily history; S&P Global shows current levels on its site",
        "S&P Global Market Intelligence (Markit) CDS pricing, Bloomberg CDSW, ICE Clear Credit "
        "settlement prices",
    ),
    "CDS:NAME": SourceSpec(
        "Single-name CDS",
        "None; DTCC swap data repository shows public trades but not a spread curve",
        "S&P Global Market Intelligence (Markit) CDS pricing, Bloomberg CDSW",
    ),
    "CRYPTO": SourceSpec(
        "Crypto spot",
        "Coinbase Exchange candles (wired), CoinGecko and Binance public APIs",
        "Kaiko, Coin Metrics, CryptoCompare, Bloomberg",
    ),
    "VOL:EQIDX": SourceSpec(
        "Equity index vol surfaces",
        "At-the-money level only: Cboe VIX and VXN, VSTOXX, VDAX-NEW, Nikkei VI (VFTSE was "
        "discontinued); Yahoo options chains give today's quotes, no history",
        "Cboe DataShop, OptionMetrics IvyDB, Bloomberg OVDV, LSEG, IVolatility, Eurex",
        "Free indices give a level and a short term structure, no skew",
    ),
    "VOL:EQ": SourceSpec(
        "Single-stock vol surfaces",
        "Yahoo Finance and Cboe delayed options chains: today's quotes, no history",
        "OptionMetrics IvyDB, ORATS, IVolatility, Cboe DataShop, Bloomberg OVDV",
    ),
    "VOL:FX": SourceSpec(
        "FX vol surfaces",
        "None for OTC surfaces; CME listed FX option settlements for today only",
        PAID_VOL + ", JP Morgan DataQuery",
        "OTC FX vol is broker data; the simulator is the only history",
    ),
    "VOL:CMD": SourceSpec(
        "Commodity vol surfaces",
        "At-the-money level only: Cboe OVX (oil) and GVZ (gold); CME option settlements for today only",
        "CME DataMine option settlements, ICE, Bloomberg OVDV, LSEG",
    ),
    "SWVOL": SourceSpec(
        "Swaption cubes",
        "None; DTCC swap data repository shows public swaption trades but not a cube",
        "Bloomberg VCUB, LSEG, TP ICAP and Tradition broker cubes",
        "Normal vol cube is broker data; the simulator is the only history",
    ),
}
ENERGY = {"BRENT", "WTI", "NATGAS"}


def _day(x: Any) -> str | None:
    """Provenance dates are stored as timestamps; the page shows the day."""
    return None if x is None or pd.isna(x) else str(x)[:10]


def family_of(factor_id: str) -> str:
    """``IR:USD``, ``VOL:SPX``, ``EQ:AAPL``: the first two parts of the id."""
    parts = factor_id.split(":")
    return ":".join(parts[:2])


def _spec_key(f: RiskFactor, underlying_kind: dict[str, str]) -> str:
    kind = f.factor_id.split(":")[0]
    if kind == "IR":
        return f"IR:{f.currency}"
    if kind == "CMD":
        return "CMD:ENERGY" if f.underlying in ENERGY else "CMD:METAL"
    if kind == "CDS":
        return "CDS:INDEX" if "." in f.underlying else "CDS:NAME"
    if kind == "VOL":
        return "VOL:" + underlying_kind.get(f.underlying, "EQ")
    if kind == "SWVOL":
        return "SWVOL"
    return kind


def _spec_for(key: str) -> SourceSpec:
    if key in _SPECS:
        return _SPECS[key]
    if key.startswith("IR:"):
        return SourceSpec(f"{key[3:]} rates", "Central bank or debt-office statistics", PAID_RATES)
    return SourceSpec(key, "None identified", "Bloomberg, LSEG")


def market_data_sources(factors: list[RiskFactor], provenance: pd.DataFrame | None) -> dict[str, Any]:
    """One row per stored factor with its status, current source and candidate sources, plus
    one row per family (curve, surface, cube or single factor) and the totals. A family is
    PARTIAL when its nodes do not share one status, e.g. a curve whose front month is wired."""
    coverage = adapter_coverage()
    prov: dict[str, dict[str, Any]] = {}
    if provenance is not None and not provenance.empty:
        for r in provenance.to_dict("records"):
            prov[str(r["factor_id"])] = r
    underlying_kind = {}
    for f in factors:
        kind = f.factor_id.split(":")[0]
        if kind in ("EQIDX", "EQ", "FX", "CMD"):
            underlying_kind[f.underlying] = kind
    rows: list[dict[str, Any]] = []
    for f in factors:
        p = prov.get(f.factor_id)
        adapter = coverage.get(f.factor_id)
        status = STATUS_REAL if p else STATUS_AVAILABLE if adapter else STATUS_SYNTHETIC
        spec = _spec_for(_spec_key(f, underlying_kind))
        rows.append(
            {
                "factor_id": f.factor_id,
                "family": family_of(f.factor_id),
                "factor_type": f.factor_type.value,
                "asset_class": f.asset_class,
                "currency": f.currency,
                "underlying": f.underlying,
                "group": spec.group,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "source": str(p["source"]) if p else SIMULATOR,
                "adapter": adapter,
                "fetched_at": str(p["fetched_at"]) if p else None,
                "first_date": _day(p["first_date"]) if p else None,
                "last_date": _day(p["last_date"]) if p else None,
                "row_count": int(p["row_count"]) if p else None,
                "free_source": spec.free,
                "paid_source": spec.paid,
                "notes": spec.notes,
            }
        )
    families: dict[str, dict[str, Any]] = {}
    for r in rows:
        fam = families.setdefault(
            r["family"],
            {
                "family": r["family"],
                "group": r["group"],
                "factor_type": r["factor_type"],
                "asset_class": r["asset_class"],
                "currency": r["currency"],
                "underlying": r["underlying"],
                "factors": 0,
                "real": 0,
                "available": 0,
                "synthetic": 0,
                "sources": set(),
                "adapters": set(),
                "last_date": None,
                "free_source": r["free_source"],
                "paid_source": r["paid_source"],
                "notes": r["notes"],
            },
        )
        fam["factors"] += 1
        fam[r["status"].lower()] += 1
        fam["sources"].add(r["source"])
        if r["adapter"]:
            fam["adapters"].add(r["adapter"])
        if r["last_date"] and (fam["last_date"] is None or r["last_date"] > fam["last_date"]):
            fam["last_date"] = r["last_date"]
    fam_rows = []
    for fam in families.values():
        if fam["real"] == fam["factors"]:
            status = STATUS_REAL
        elif fam["available"] == fam["factors"]:
            status = STATUS_AVAILABLE
        elif fam["synthetic"] == fam["factors"]:
            status = STATUS_SYNTHETIC
        else:
            status = STATUS_PARTIAL
        fam["status"] = status
        fam["status_label"] = STATUS_LABELS[status]
        fam["sources"] = sorted(fam["sources"])
        fam["adapters"] = sorted(fam["adapters"])
        fam_rows.append(fam)
    summary = {
        "factors": len(rows),
        "real": sum(r["status"] == STATUS_REAL for r in rows),
        "available": sum(r["status"] == STATUS_AVAILABLE for r in rows),
        "synthetic": sum(r["status"] == STATUS_SYNTHETIC for r in rows),
        "families": len(fam_rows),
        "families_real": sum(f["status"] == STATUS_REAL for f in fam_rows),
        "families_synthetic": sum(f["status"] == STATUS_SYNTHETIC for f in fam_rows),
    }
    return {"record": RECORD, "rows": rows, "families": fam_rows, "summary": summary}
