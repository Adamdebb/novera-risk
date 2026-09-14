"""Coinbase Exchange public candles: BTC and ETH daily closes in USD. No key needed."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from novera.market_data.adapters.base import FetchResult

PRODUCTS = {"BTC-USD": "CRYPTO:BTC", "ETH-USD": "CRYPTO:ETH"}
BASE = "https://api.exchange.coinbase.com/products/{product}/candles"
CHUNK_DAYS = 250  # the endpoint returns at most 300 candles per call


class CoinbaseAdapter:
    name = "coinbase"

    def __init__(self, client: Any | None = None) -> None:
        import httpx

        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": "NoveraRisk/0.1"})

    def fetch(self, start: date, end: date) -> FetchResult:
        rows, fetched, errors = [], {}, {}
        for product, fid in PRODUCTS.items():
            n = 0
            cur = start
            try:
                while cur <= end:
                    stop = min(cur + timedelta(days=CHUNK_DAYS), end)
                    r = self.client.get(
                        BASE.format(product=product),
                        params={
                            "granularity": 86400,
                            "start": datetime.combine(cur, datetime.min.time()).isoformat(),
                            "end": datetime.combine(
                                stop + timedelta(days=1), datetime.min.time()
                            ).isoformat(),
                        },
                    )
                    r.raise_for_status()
                    for candle in r.json():  # [time, low, high, open, close, volume]
                        rows.append(
                            {
                                "as_of": datetime.fromtimestamp(candle[0]).date(),
                                "factor_id": fid,
                                "value": float(candle[4]),
                            }
                        )
                        n += 1
                    cur = stop + timedelta(days=1)
                fetched[fid] = n
            except Exception as e:  # noqa: BLE001
                errors[product] = f"{type(e).__name__}: {e}"
        return FetchResult(
            self.name, pd.DataFrame(rows, columns=["as_of", "factor_id", "value"]), fetched, errors
        )
