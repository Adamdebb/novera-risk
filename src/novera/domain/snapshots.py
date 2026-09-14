"""Portfolio snapshots with content-hash identifiers (docs/02-architecture.md)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from novera.domain.trades import Trade


def content_hash(payload: Any) -> str:
    """Deterministic short hash of any JSON-serialisable payload."""
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class PortfolioSnapshot(BaseModel):
    """The set of trades that exist as of a business date. Identity is its content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    business_date: date
    trades: tuple[Trade, ...]
    source: str = Field(default="SIM", description="Where the trades came from")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def snapshot_id(self) -> str:
        rows = [t.model_dump(mode="json") for t in sorted(self.trades, key=lambda t: (t.trade_id, t.version))]
        return content_hash({"business_date": self.business_date.isoformat(), "trades": rows})

    @property
    def live_trades(self) -> tuple[Trade, ...]:
        return tuple(t for t in self.trades if t.status.value == "LIVE")

    def __len__(self) -> int:
        return len(self.trades)
