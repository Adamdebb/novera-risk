"""Run records and audit events (docs/04-governance.md).

A run is identified by a time-ordered id and records every input that determines its
numbers: business date, portfolio and market snapshot ids, previous-day ids, model
versions and the configuration hash. Results are written once under that id.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

MODEL_VERSIONS: dict[str, str] = {}

USABLE_STATUSES: tuple[str, ...] = ("COMPLETED", "PARTIAL")
"""Statuses whose stored results can be read: a PARTIAL run lacks only the stages listed in
``summary["failed_stages"]`` (docs/07-eod-workflow.md, "Failure behaviour")."""


def new_run_id(prefix: str = "run") -> str:
    """Sortable id: prefix, millisecond timestamp in base36, 6 random hex chars."""
    ms = int(time.time() * 1000)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while ms:
        ms, r = divmod(ms, 36)
        out = digits[r] + out
    return f"{prefix}_{out}_{secrets.token_hex(3)}"


def config_hash(config: dict[str, Any]) -> str:
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class RunRecord:
    run_id: str
    run_type: str  # EOD, ADHOC, WHATIF
    business_date: date
    portfolio_snapshot_id: str
    market_snapshot_id: str
    previous_market_snapshot_id: str | None
    reporting_currency: str
    model_versions: dict[str, str]
    config: dict[str, Any]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    status: str = "RUNNING"  # RUNNING, COMPLETED, PARTIAL, FAILED
    verdict: str = "PENDING"  # GREEN, AMBER, RED
    summary: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        return config_hash({"config": self.config, "models": self.model_versions})

    @property
    def failed_stages(self) -> dict[str, str]:
        """Stages that failed after the core results were stored, with their error."""
        return dict(self.summary.get("failed_stages") or {})

    def finish(self, status: str = "COMPLETED") -> None:
        self.finished_at = datetime.now(UTC)
        self.status = status


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    at: datetime
    actor: str
    event_type: str  # RUN_STARTED, RUN_FINISHED, DQ_FINDING, LIMIT_BREACH, LIMIT_WARNING, ...
    subject: str  # run id, limit id, trade id
    payload: dict[str, Any]

    @classmethod
    def now(cls, actor: str, event_type: str, subject: str, **payload: Any) -> AuditEvent:
        return cls(new_run_id("evt"), datetime.now(UTC), actor, event_type, subject, payload)
