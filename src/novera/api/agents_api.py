"""Agent and Portfolio Lab operations that take a database path (they open their own
connections), shared by the local client, the HTTP API and the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novera.config import get_settings
from novera.security import Action, User, require
from novera.storage.duckdb_repository import DuckDBRepository

AGENT_METHODS = {
    "investigate_breach",
    "suggest_scenarios",
    "draft_validation",
    "propose_csa",
    "approve_csa",
    "reject_csa",
    "agent_notes",
    "run_lab",
    "labs",
    "lab",
    "problem_catalogue",
}


class AgentOps:
    """Agents and the Portfolio Lab. ``user`` is the signed-in user of an API or dashboard call,
    checked before every write (ADR 0006); None is the local operator at the CLI."""

    def __init__(self, db_path: str, user: User | None = None) -> None:
        self.db_path = str(db_path)
        self.user = user

    def _require(self, action: Action) -> None:
        if self.user is not None:
            require(self.user, action)

    def _actor(self, actor: str | None) -> str:
        if self.user is not None:
            return self.user.name
        if not actor or not actor.strip():
            raise ValueError("name the actor")
        return actor

    # --- agents -------------------------------------------------------------------------
    def investigate_breach(self, breach_id: str, attach: bool = True) -> dict[str, Any]:
        from novera.ai.agents import investigate_breach

        self._require(Action.AGENT_RUN)
        return investigate_breach(self.db_path, breach_id, attach=attach).to_dict()

    def suggest_scenarios(self, run_id: str | None = None, n: int = 4) -> dict[str, Any]:
        from novera.ai.agents import suggest_scenarios

        self._require(Action.AGENT_RUN)
        return suggest_scenarios(self.db_path, run_id, n=n).to_dict()

    def draft_validation(
        self, run_id: str | None = None, records: list[str] | None = None, run_tests: bool = False
    ) -> dict[str, Any]:
        from novera.ai.agents import draft_validation_report

        self._require(Action.AGENT_RUN)
        out_dir = Path(get_settings().data_dir) / "reports"
        note, path = draft_validation_report(self.db_path, run_id, records, run_tests, out_dir)
        d = note.to_dict()
        d["path"] = str(path) if path else None
        return d

    def propose_csa(self, path: str) -> dict[str, Any]:
        from novera.ai.agents import propose_csa

        self._require(Action.AGENT_RUN)
        return propose_csa(self.db_path, path).to_dict()

    def approve_csa(self, note_id: str, actor: str | None = None) -> dict[str, Any]:
        from novera.ai.agents import approve_csa

        self._require(Action.CSA_DECIDE)
        return approve_csa(self.db_path, note_id, self._actor(actor))

    def reject_csa(self, note_id: str, actor: str | None = None, reason: str = "") -> dict[str, Any]:
        from novera.ai.agents import reject_csa

        self._require(Action.CSA_DECIDE)
        reject_csa(self.db_path, note_id, self._actor(actor), reason)
        return {"note_id": note_id, "status": "REJECTED"}

    def agent_notes(self, kind: str | None = None, subject: str | None = None, limit: int = 50) -> list[dict]:
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            notes = repo.load_agent_notes(kind, subject, limit)
        for n in notes:
            n.pop("evidence", None) if len(str(n.get("evidence", ""))) > 20000 else None
        return notes

    # --- portfolio lab --------------------------------------------------------------------
    def problem_catalogue(self) -> dict[str, Any]:
        from novera.lab import PROBLEM_CATALOGUE
        from novera.simulation.market_data import MARKET_PROBLEMS
        from novera.simulation.trades import BANK_PROBLEMS, FUND_PROBLEMS

        return {
            "bank": [
                {"name": p, **{k: v for k, v in PROBLEM_CATALOGUE[p].items() if k == "title"}}
                for p in BANK_PROBLEMS
            ],
            "hedge_fund": [
                {"name": p, **{k: v for k, v in PROBLEM_CATALOGUE[p].items() if k == "title"}}
                for p in FUND_PROBLEMS
            ],
            "market": [{"name": p, "title": PROBLEM_CATALOGUE[p]["title"]} for p in MARKET_PROBLEMS],
        }

    def run_lab(self, spec: dict[str, Any]) -> dict[str, Any]:
        from datetime import date

        from novera.lab import LabSpec, run_lab

        self._require(Action.LAB_RUN)
        d = dict(spec)
        if isinstance(d.get("business_date"), str):
            d["business_date"] = date.fromisoformat(d["business_date"])
        for k in ("problems", "market_problems"):
            if k in d and d[k] is not None:
                d[k] = tuple(d[k])
        return run_lab(LabSpec(**d)).to_dict()

    def labs(self) -> list[dict[str, Any]]:
        from novera.lab import list_labs

        return list_labs()

    def lab(self, name: str) -> dict[str, Any] | None:
        from novera.lab import LabSpec

        p = LabSpec(name=name).db_path()
        if not p.exists():
            return None
        with DuckDBRepository(p, read_only=True) as repo:
            rec = repo.load_lab(name)
        return {"name": name, "db_path": str(p), **(rec or {})}
