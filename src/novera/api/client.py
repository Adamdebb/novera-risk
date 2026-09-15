"""Client used by the UI. Local mode calls the service in-process; HTTP mode calls the API.
Both expose the same methods, so screens never know which one they talk to."""

from __future__ import annotations

from typing import Any, Protocol

from novera.api.agents_api import AGENT_METHODS
from novera.api.service import RiskService, RiskWriteService
from novera.storage.duckdb_repository import DuckDBRepository


class RiskClient(Protocol):
    def runs(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def summary(self, run_id: str | None = None) -> dict[str, Any]: ...
    def positions(
        self, run_id: str | None = None, by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]: ...
    def var_by(
        self, by: str, run_id: str | None = None, method: str = "historical_full_revaluation", **filters: str
    ) -> list[dict[str, Any]]: ...
    def var_summary(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def var_scenarios(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def sensitivities(
        self, run_id: str | None = None, measure: str = "DV01", by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]: ...
    def stress(
        self, run_id: str | None = None, by: str | None = "asset_class", **filters: str
    ) -> list[dict]: ...
    def limits(
        self, run_id: str | None = None, status: str | None = None, level: str | None = None
    ) -> list[dict]: ...
    def dq(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def pnl(self, run_id: str | None = None, by: str | None = None, **filters: str) -> dict[str, Any]: ...
    def trade(self, trade_id: str, run_id: str | None = None) -> dict[str, Any]: ...
    def audit(self, subject: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def organisation(self) -> dict[str, Any]: ...


WRITE_METHODS = {
    "acknowledge",
    "escalate",
    "comment",
    "close",
    "request_increase",
    "decide_increase",
    "cancel_increase",
}
COPILOT_METHODS = {"ask", "commentary", "copilot_history", "copilot_provider"}
PACK_METHODS = {"risk_pack"}


class LocalClient:
    """Opens a short-lived connection per call: read-only for reads so it coexists with a
    writer (the EOD run), writable only for workflow actions."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path

    def _call(self, name: str, *a: Any, **kw: Any) -> Any:
        if name in COPILOT_METHODS:
            from novera.ai import Copilot, make_provider

            if name == "copilot_provider":
                p = make_provider()
                return {"provider": p.name, "model": p.model}
            c = Copilot(self.db_path)
            if name == "copilot_history":
                return c.history(*a, **kw)
            return getattr(c, name)(*a, **kw).to_dict()
        if name in AGENT_METHODS:
            from novera.api.agents_api import AgentOps

            return getattr(AgentOps(self.db_path), name)(*a, **kw)
        if name in WRITE_METHODS:
            with DuckDBRepository(self.db_path) as repo:
                return getattr(RiskWriteService(repo), name)(*a, **kw)
        if name in PACK_METHODS:
            from novera.config import get_settings

            kw.setdefault("out_dir", str(get_settings().data_dir / "reports"))
            with DuckDBRepository(self.db_path, read_only=True) as repo:
                return RiskService(repo).risk_pack(*a, **kw)
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            return getattr(RiskService(repo), name)(*a, **kw)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: self._call(name, *a, **kw)


class HttpClient:
    def __init__(self, base_url: str) -> None:
        import httpx

        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=30)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def _rid(self, run_id: str | None) -> str:
        return run_id or "latest"

    def runs(self, limit: int = 20):
        return self._get("/runs", limit=limit)

    def summary(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/summary")

    def positions(self, run_id=None, by="desk_id", **f):
        return self._get(f"/runs/{self._rid(run_id)}/positions", by=by, **f)

    def var_by(self, by, run_id=None, method="historical_full_revaluation", **f):
        return self._get(f"/runs/{self._rid(run_id)}/var", by=by, method=method, **f)

    def var_summary(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/var/summary")

    def var_scenarios(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/var/scenarios")

    def sensitivities(self, run_id=None, measure="DV01", by="desk_id", **f):
        return self._get(f"/runs/{self._rid(run_id)}/sensitivities", measure=measure, by=by, **f)

    def stress(self, run_id=None, by="asset_class", **f):
        return self._get(f"/runs/{self._rid(run_id)}/stress", by=by, **f)

    def limits(self, run_id=None, status=None, level=None):
        return self._get(f"/runs/{self._rid(run_id)}/limits", status=status, level=level)

    def dq(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/dq")

    def pnl(self, run_id=None, by=None, **f):
        return self._get(f"/runs/{self._rid(run_id)}/pnl", by=by, **f)

    def trade(self, trade_id, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/trades/{trade_id}")

    def audit(self, subject=None, limit=100):
        return self._get("/audit", subject=subject, limit=limit)

    def organisation(self):
        return self._get("/organisation")

    def _post(self, path: str, **body: Any) -> Any:
        r = self.http.post(path, json=body)
        if r.status_code == 409:
            from novera.limits import WorkflowError

            raise WorkflowError(r.json().get("detail", r.text))
        r.raise_for_status()
        return r.json()

    def breaches(self, open_only=True, limit_id=None):
        return self._get("/breaches", open_only=open_only, limit_id=limit_id)

    def breach(self, breach_id):
        return self._get(f"/breaches/{breach_id}")

    def acknowledge(self, breach_id, actor, comment=""):
        return self._post(f"/breaches/{breach_id}/acknowledge", actor=actor, comment=comment)

    def escalate(self, breach_id, actor, to=None, comment=""):
        return self._post(f"/breaches/{breach_id}/escalate", actor=actor, to=to, comment=comment)

    def comment(self, breach_id, actor, text):
        return self._post(f"/breaches/{breach_id}/comment", actor=actor, comment=text)

    def close(self, breach_id, actor, reason, comment=""):
        return self._post(f"/breaches/{breach_id}/close", actor=actor, reason=reason, comment=comment)

    def increases(self, status=None, limit_id=None):
        return self._get("/increases", status=status, limit_id=limit_id)

    def request_increase(
        self, limit_id, new_amount, expires_on, requested_by, rationale, effective_from=None, breach_id=None
    ):
        return self._post(
            "/increases",
            limit_id=limit_id,
            new_amount=new_amount,
            expires_on=expires_on,
            requested_by=requested_by,
            rationale=rationale,
            effective_from=effective_from,
            breach_id=breach_id,
        )

    def decide_increase(self, increase_id, approver, approve, comment=""):
        return self._post(
            f"/increases/{increase_id}/decide", approver=approver, approve=approve, comment=comment
        )

    def cancel_increase(self, increase_id, actor):
        return self._post(f"/increases/{increase_id}/cancel", actor=actor)

    def compare(self, run_a, run_b, by="asset_class"):
        return self._get("/compare", run_a=run_a, run_b=run_b, by=by)

    def ask(self, question, run_id=None, session_id=None):
        r = self.http.post(
            "/copilot/ask",
            json={"question": question, "run_id": run_id, "session_id": session_id},
            timeout=300,
        )
        r.raise_for_status()
        return r.json()

    def commentary(self, run_id=None):
        r = self.http.post("/copilot/commentary", params={"run_id": run_id} if run_id else None, timeout=300)
        r.raise_for_status()
        return r.json()

    def copilot_history(self, limit=50):
        return self._get("/copilot/history", limit=limit)

    def copilot_provider(self):
        return self._get("/copilot/provider")

    # --- agents and lab (long-running: generous timeouts) ---
    def _post_long(self, path, **body):
        r = self.http.post(path, json=body, timeout=1800)
        r.raise_for_status()
        return r.json()

    def investigate_breach(self, breach_id, attach=True):
        return self._post_long("/agents/investigate-breach", breach_id=breach_id, attach=attach)

    def suggest_scenarios(self, run_id=None, n=4):
        return self._post_long("/agents/suggest-scenarios", run_id=run_id, n=n)

    def draft_validation(self, run_id=None, records=None, run_tests=False):
        return self._post_long(
            "/agents/draft-validation", run_id=run_id, records=records, run_tests=run_tests
        )

    def propose_csa(self, path):
        return self._post_long("/agents/propose-csa", path=path)

    def approve_csa(self, note_id, actor):
        return self._post_long("/agents/approve-csa", note_id=note_id, actor=actor)

    def reject_csa(self, note_id, actor, reason=""):
        return self._post_long("/agents/reject-csa", note_id=note_id, actor=actor, reason=reason)

    def agent_notes(self, kind=None, subject=None, limit=50):
        return self._get("/agents/notes", kind=kind, subject=subject, limit=limit)

    def problem_catalogue(self):
        return self._get("/lab/catalogue")

    def run_lab(self, spec):
        return self._post_long("/lab/run", **spec)

    def labs(self):
        return self._get("/lab")

    def lab(self, name):
        return self._get(f"/lab/{name}")

    def alerts(self, limit=200, status=None, severity=None):
        return self._get("/alerts", limit=limit, status=status, severity=severity)

    def jobs(self, limit=100):
        return self._get("/jobs", limit=limit)

    def reconciliation(self, run_id=None):
        r = self.http.get(f"/runs/{self._rid(run_id)}/reconciliation")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def provenance(self):
        return self._get("/market-data/provenance")

    def concentration(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/concentration")

    def liquidity(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/liquidity")

    def lookthrough(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/lookthrough")

    def market_data_proxies(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/market-data-proxies")

    def backtest(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/backtest")

    def counterparties(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/counterparties")

    def counterparty(self, counterparty_id, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/counterparties/{counterparty_id}")

    def csa_what_if(self, netting_set_id, run_id=None, **terms):
        r = self.http.post(
            f"/runs/{self._rid(run_id)}/csa-what-if", json={"netting_set_id": netting_set_id, **terms}
        )
        r.raise_for_status()
        return r.json()

    def fund(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/fund")

    def capital(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/capital")

    def risk_pack(self, run_id=None, out_dir=None, pdf=True):
        r = self.http.post(f"/runs/{self._rid(run_id)}/risk-pack", params={"pdf": pdf}, timeout=300)
        r.raise_for_status()
        return r.json()


def make_client(settings, db_path=None) -> RiskClient:
    import os

    url = os.environ.get("NOVERA_API_URL")
    return HttpClient(url) if url else LocalClient(db_path or settings.db_path)  # type: ignore[return-value]
