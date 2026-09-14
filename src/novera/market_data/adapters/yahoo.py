"""Yahoo Finance chart endpoint: equities, indices, FX and commodity front-month futures.

Uses the public chart JSON (no key). Factor mapping below; unmapped names stay synthetic.
Futures give one node per commodity; the other curve nodes are scaled by the synthetic
curve shape so the curve stays consistent (documented in SIM-001).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from novera.market_data.adapters.base import FetchResult

SYMBOLS: dict[str, str] = {
    "^GSPC": "EQIDX:SPX",
    "^NDX": "EQIDX:NDX",
    "^STOXX50E": "EQIDX:SX5E",
    "^GDAXI": "EQIDX:DAX",
    "^FTSE": "EQIDX:FTSE",
    "^N225": "EQIDX:NKY",
    "AAPL": "EQ:AAPL",
    "MSFT": "EQ:MSFT",
    "NVDA": "EQ:NVDA",
    "JPM": "EQ:JPM",
    "XOM": "EQ:XOM",
    "PFE": "EQ:PFE",
    "TSLA": "EQ:TSLA",
    "BA": "EQ:BA",
    "SAP.DE": "EQ:SAP",
    "ASML.AS": "EQ:ASML",
    "SIE.DE": "EQ:SIE",
    "TTE.PA": "EQ:TTE",
    "BNP.PA": "EQ:BNP",
    "NESN.SW": "EQ:NESN",
    "HSBA.L": "EQ:HSBA",
    "SHEL.L": "EQ:SHEL",
    "EURUSD=X": "FX:EURUSD",
    "GBPUSD=X": "FX:GBPUSD",
    "JPY=X": "FX:USDJPY",
    "AUDUSD=X": "FX:AUDUSD",
    "CHF=X": "FX:USDCHF",
    "CAD=X": "FX:USDCAD",
    "NZDUSD=X": "FX:NZDUSD",
    "EURGBP=X": "FX:EURGBP",
    "MXN=X": "FX:USDMXN",
    "BRL=X": "FX:USDBRL",
    "ZAR=X": "FX:USDZAR",
    "INR=X": "FX:USDINR",
    "TRY=X": "FX:USDTRY",
    "SGD=X": "FX:USDSGD",
    "BZ=F": "CMD:BRENT:1M",
    "CL=F": "CMD:WTI:1M",
    "NG=F": "CMD:NATGAS:1M",
    "GC=F": "CMD:GOLD:1M",
    "SI=F": "CMD:SILVER:1M",
    "HG=F": "CMD:COPPER:1M",
}
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NoveraRisk/0.1)"}


class YahooAdapter:
    name = "yahoo"

    def __init__(self, client: Any | None = None, symbols: dict[str, str] | None = None) -> None:
        import httpx

        self.client = client or httpx.Client(timeout=30, headers=HEADERS)
        self.symbols = symbols or SYMBOLS

    def fetch(self, start: date, end: date) -> FetchResult:
        rows, fetched, errors = [], {}, {}
        p1 = int(datetime.combine(start, datetime.min.time()).timestamp())
        p2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp())
        for symbol, fid in self.symbols.items():
            try:
                r = self.client.get(
                    BASE.format(symbol=symbol),
                    params={"period1": p1, "period2": p2, "interval": "1d", "events": "div,splits"},
                )
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                ts = res.get("timestamp", [])
                closes = res["indicators"]["quote"][0].get("close", [])
                n = 0
                for t, c in zip(ts, closes, strict=False):
                    if c is None:
                        continue
                    rows.append(
                        {"as_of": datetime.fromtimestamp(t).date(), "factor_id": fid, "value": float(c)}
                    )
                    n += 1
                fetched[fid] = n
            except Exception as e:  # noqa: BLE001
                errors[symbol] = f"{type(e).__name__}: {e}"
        return FetchResult(
            self.name, pd.DataFrame(rows, columns=["as_of", "factor_id", "value"]), fetched, errors
        )
