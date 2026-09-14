"""FRED (St. Louis Fed): US Treasury constant-maturity yields as the USD curve.

Treasury par yields stand in for zero rates (documented approximation, SIM-001 note).
Needs a free API key: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from novera.market_data.adapters.base import FetchResult

SERIES: dict[str, str] = {
    "DGS1MO": "IR:USD:1M",
    "DGS3MO": "IR:USD:3M",
    "DGS6MO": "IR:USD:6M",
    "DGS1": "IR:USD:1Y",
    "DGS2": "IR:USD:2Y",
    "DGS3": "IR:USD:3Y",
    "DGS5": "IR:USD:5Y",
    "DGS7": "IR:USD:7Y",
    "DGS10": "IR:USD:10Y",
    "DGS20": "IR:USD:20Y",
    "DGS30": "IR:USD:30Y",
}
BASE = "https://api.stlouisfed.org/fred/series/observations"


class FredAdapter:
    name = "fred"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        import httpx

        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def fetch(self, start: date, end: date) -> FetchResult:
        rows, fetched, errors = [], {}, {}
        for series, fid in SERIES.items():
            try:
                r = self.client.get(
                    BASE,
                    params={
                        "series_id": series,
                        "api_key": self.api_key,
                        "file_type": "json",
                        "observation_start": str(start),
                        "observation_end": str(end),
                    },
                )
                r.raise_for_status()
                obs = r.json().get("observations", [])
                n = 0
                for o in obs:
                    if o.get("value") in (None, ".", ""):
                        continue
                    rows.append(
                        {
                            "as_of": pd.Timestamp(o["date"]).date(),
                            "factor_id": fid,
                            "value": float(o["value"]) / 100.0,
                        }
                    )
                    n += 1
                fetched[fid] = n
            except Exception as e:  # noqa: BLE001
                errors[series] = f"{type(e).__name__}: {e}"
        frame = pd.DataFrame(rows, columns=["as_of", "factor_id", "value"])
        # 15Y is not published: interpolate between 10Y and 20Y so the curve keeps its node set.
        if len(frame):
            w = frame.pivot(index="as_of", columns="factor_id", values="value")
            if "IR:USD:10Y" in w and "IR:USD:20Y" in w:
                w["IR:USD:15Y"] = (w["IR:USD:10Y"] + w["IR:USD:20Y"]) / 2
                frame = w.stack().rename("value").reset_index().rename(columns={"level_1": "factor_id"})
                frame = frame[["as_of", "factor_id", "value"]]
                fetched["IR:USD:15Y"] = int(frame[frame["factor_id"] == "IR:USD:15Y"].shape[0])
        return FetchResult(self.name, frame, fetched, errors)
