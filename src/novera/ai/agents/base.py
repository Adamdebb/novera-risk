"""Shared plumbing for the agents (AI-002): an agent gathers evidence deterministically from
stored runs and the engine, asks the provider to draft prose from that evidence only, and
stores the note with its evidence so every sentence can be traced to a number."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from novera.ai.copilot import make_provider
from novera.ai.provider import Provider
from novera.config import Settings, get_settings
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent, new_run_id

MODEL_VERSION = "1.0.0"


@dataclass
class AgentNote:
    note_id: str
    kind: str  # BREACH_INVESTIGATION, SCENARIO_SUGGESTION, MODEL_VALIDATION, CSA_INGESTION
    subject: str  # breach id, run id, record id or document path
    run_id: str | None
    provider: str
    model: str
    text: str
    evidence: dict[str, Any]
    status: str = "DRAFT"  # DRAFT, ATTACHED, APPROVED, REJECTED
    usage: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "kind": self.kind,
            "subject": self.subject,
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "text": self.text,
            "evidence": self.evidence,
            "usage": self.usage,
            "seconds": round(self.seconds, 2),
            "at": self.at.isoformat(),
        }


class Agent:
    """Base: subclasses implement ``gather`` (deterministic) and set ``kind`` and ``instructions``."""

    kind = "AGENT"
    instructions = ""

    def __init__(
        self, db_path: str, provider: Provider | None = None, settings: Settings | None = None
    ) -> None:
        self.db_path = str(db_path)
        self.settings = settings or get_settings()
        self.provider = provider or make_provider(self.settings)

    def gather(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(
        self,
        subject: str,
        run_id: str | None = None,
        persist: bool = True,
        evidence: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AgentNote:
        t0 = time.perf_counter()
        evidence = evidence if evidence is not None else self.gather(**kwargs)
        text, usage = self.provider.draft(self.kind, evidence, self.instructions)
        note = AgentNote(
            new_run_id("note"),
            self.kind,
            subject,
            run_id or evidence.get("run_id"),
            self.provider.name,
            self.provider.model,
            text,
            evidence,
            usage=usage,
            seconds=time.perf_counter() - t0,
        )
        if persist:
            store_note(self.db_path, note)
        return note


def store_note(db_path: str, note: AgentNote) -> None:
    with DuckDBRepository(db_path) as repo:
        repo.init_schema()
        repo.save_agent_note(note.to_dict())
        repo.save_audit_events(
            [
                AuditEvent.now(
                    "agent",
                    "AGENT_NOTE",
                    note.note_id,
                    kind=note.kind,
                    note_subject=note.subject,
                    run_id=note.run_id,
                    provider=note.provider,
                    model=note.model,
                    status=note.status,
                )
            ]
        )


def _m(x: Any) -> float | None:
    return None if x is None else round(float(x) / 1e6, 3)
