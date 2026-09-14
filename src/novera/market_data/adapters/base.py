from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

import pandas as pd


@dataclass
class FetchResult:
    source: str
    frame: pd.DataFrame  # as_of, factor_id, value
    fetched: dict[str, int] = field(default_factory=dict)  # factor_id -> rows
    errors: dict[str, str] = field(default_factory=dict)  # external symbol -> error


class Adapter(Protocol):
    name: str

    def fetch(self, start: date, end: date) -> FetchResult: ...


def fetch_all(adapters: list[Adapter], start: date, end: date) -> list[FetchResult]:
    out: list[FetchResult] = []
    for a in adapters:
        try:
            out.append(a.fetch(start, end))
        except Exception as e:  # noqa: BLE001 - one source failing must not stop the others
            out.append(
                FetchResult(
                    a.name,
                    pd.DataFrame(columns=["as_of", "factor_id", "value"]),
                    errors={"*": f"{type(e).__name__}: {e}"},
                )
            )
    return out


def apply_real_history(repo: Any, results: list[FetchResult]) -> dict[str, Any]:
    """Merge fetched rows into the history (replacing synthetic values on the same dates) and
    record provenance. Returns a summary."""
    frames = [r.frame for r in results if len(r.frame)]
    if not frames:
        return {
            "rows": 0,
            "factors": 0,
            "sources": [r.source for r in results],
            "errors": {r.source: r.errors for r in results if r.errors},
        }
    df = pd.concat(frames, ignore_index=True).drop_duplicates(["as_of", "factor_id"], keep="last")
    n = repo.save_market_history(df)
    prov = [
        {
            "factor_id": fid,
            "source": r.source,
            "fetched_at": datetime.now(UTC).isoformat(),
            "first": str(g["as_of"].min()),
            "last": str(g["as_of"].max()),
            "rows": int(len(g)),
        }
        for r in results
        if len(r.frame)
        for fid, g in r.frame.groupby("factor_id")
    ]
    repo.save_market_provenance(prov)
    return {
        "rows": n,
        "factors": int(df["factor_id"].nunique()),
        "sources": [r.source for r in results],
        "errors": {r.source: r.errors for r in results if r.errors},
    }
