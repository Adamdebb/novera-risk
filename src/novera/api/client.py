"""Client used by the UI. Local mode calls the service in-process; HTTP mode calls the API.
Both expose the same methods, so screens never know which one they talk to."""

from __future__ import annotations

from typing import Any, Protocol

from novera.api.agents_api import AGENT_METHODS
from novera.api.errors import ApiError, NotFoundError, error_from_problem, translate
from novera.api.service import RiskService, RiskWriteService
from novera.storage.duckdb_repository import DuckDBRepository


class RiskClient(Protocol):
    def runs(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def summary(self, run_id: str | None = None) -> dict[str, Any]: ...
    def positions(
        self, run_id: str | None = None, by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]: ...
    def var_by(
        self,
        by: str,
        run_id: str | None = None,
        method: str = "historical_full_revaluation",
        measure_id: str | None = None,
        **filters: str,
    ) -> list[dict[str, Any]]: ...
    def var_measure_scenarios(
        self, run_id: str | None = None, measure_id: str | None = None
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
    def organisation(self, firm_id: str | None = None) -> dict[str, Any]: ...
    def limit_hierarchy(self, run_id: str | None = None) -> dict[str, Any]: ...
    def trade_extract_options(self, run_id: str | None = None) -> dict[str, Any]: ...
    def trade_extract(self, run_id: str | None = None, **filters: Any) -> dict[str, Any]: ...
    def trade_extract_csv(self, run_id: str | None = None, **filters: Any) -> bytes: ...
    def counterparty_reference(self) -> dict[str, Any]: ...
    def product_reference(self) -> dict[str, Any]: ...
    def model_inventory(self) -> dict[str, Any]: ...
    def measure_reference(self) -> dict[str, Any]: ...
    def risk_factor_reference(self) -> dict[str, Any]: ...
    def market_data_sources(self) -> dict[str, Any]: ...
    def stress_library(self) -> dict[str, Any]: ...
    def rerun_options(self) -> dict[str, Any]: ...
    def signoff_status(self, run_id: str | None = None) -> dict[str, Any]: ...
    def signoff_policy(self) -> dict[str, Any]: ...
    def signoff_queue(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def sign_metric(self, run_id: str, metric_id: str, actor: str, comment: str = "") -> dict[str, Any]: ...
    def reject_metric(self, run_id: str, metric_id: str, actor: str, comment: str = "") -> dict[str, Any]: ...
    def set_signoff_policy(self, actor: str, required: list[str], comment: str = "") -> dict[str, Any]: ...
    def var_setup(self) -> dict[str, Any]: ...
    def set_var_setup(
        self, actor: str, measures: list[dict[str, Any]], comment: str = ""
    ) -> dict[str, Any]: ...
    def apply_var_template(self, template: str, actor: str, comment: str = "") -> dict[str, Any]: ...
    def rerun_stage(self, run_id: str, stage: str, actor: str, reason: str = "") -> dict[str, Any]: ...


WRITE_METHODS = {
    "acknowledge",
    "escalate",
    "comment",
    "close",
    "request_increase",
    "decide_increase",
    "cancel_increase",
    "rerun_stage",
    "sign_metric",
    "reject_metric",
    "set_signoff_policy",
    "set_var_setup",
    "apply_var_template",
}
ANALYST_METHODS = {"ask", "commentary", "analyst_history", "analyst_provider"}
PACK_METHODS = {"risk_pack"}


class LocalClient:
    """Opens a short-lived connection per call: read-only for reads so it coexists with a
    writer (the EOD run), writable only for workflow actions."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path

    def _call(self, name: str, *a: Any, **kw: Any) -> Any:
        try:
            return self._dispatch(name, *a, **kw)
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001 - engine errors become the API's errors
            err = translate(e)
            if err is None:
                raise
            raise err from e

    def trade_extract_csv(self, run_id: str | None = None, **filters: Any) -> bytes:
        return self._call("trade_extract_csv", run_id, **filters).encode("utf-8")

    def risk_pack_content(self, run_id: str | None = None, fmt: str = "html") -> bytes:
        from pathlib import Path

        files = self._call("risk_pack", run_id, pdf=fmt == "pdf")
        path = files.get(fmt)
        if not path or not Path(path).exists():
            raise NotFoundError(f"no {fmt} risk pack for run {run_id}", code="RISK_PACK_NOT_BUILT")
        return Path(path).read_bytes()

    def _dispatch(self, name: str, *a: Any, **kw: Any) -> Any:
        if name in ANALYST_METHODS:
            from novera.ai import Analyst, make_provider

            if name == "analyst_provider":
                p = make_provider()
                return {"provider": p.name, "model": p.model}
            c = Analyst(self.db_path)
            if name == "analyst_history":
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

    @staticmethod
    def _check(r: Any) -> Any:
        """Raise the typed error carried by a problem document; return the JSON body otherwise."""
        if r.status_code >= 400:
            try:
                payload = r.json()
            except ValueError:
                payload = {}
            raise error_from_problem(payload if isinstance(payload, dict) else {}, r.status_code, r.text)
        return r.json()

    def _get(self, path: str, **params: Any) -> Any:
        return self._check(self.http.get(path, params={k: v for k, v in params.items() if v is not None}))

    def _rid(self, run_id: str | None) -> str:
        return run_id or "latest"

    def runs(self, limit: int = 20):
        return self._get("/runs", limit=limit)

    def summary(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/summary")

    def positions(self, run_id=None, by="desk_id", **f):
        return self._get(f"/runs/{self._rid(run_id)}/positions", by=by, **f)

    def var_by(self, by, run_id=None, method="historical_full_revaluation", measure_id=None, **f):
        return self._get(f"/runs/{self._rid(run_id)}/var", by=by, method=method, measure_id=measure_id, **f)

    def var_measure_scenarios(self, run_id=None, measure_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/var/measure-scenarios", measure_id=measure_id)

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

    def organisation(self, firm_id=None):
        return self._get("/organisation", firm_id=firm_id)

    def counterparty_reference(self):
        return self._get("/reference/counterparties")

    def product_reference(self):
        return self._get("/reference/products")

    def model_inventory(self):
        return self._get("/reference/model-inventory")

    def measure_reference(self):
        return self._get("/reference/measures")

    def risk_factor_reference(self):
        return self._get("/reference/risk-factors")

    def market_data_sources(self):
        return self._get("/reference/market-data-sources")

    def stress_library(self):
        return self._get("/reference/stress-library")

    def rerun_options(self):
        return self._get("/admin/rerun/options")

    def signoff_status(self, run_id=None):
        return self._get(f"/runs/{run_id or 'latest'}/signoff")

    def signoff_policy(self):
        return self._get("/admin/signoff/policy")

    def signoff_queue(self, limit=20):
        return self._get("/signoff/queue", limit=limit)

    def sign_metric(self, run_id, metric_id, actor, comment=""):
        return self._post(
            f"/runs/{run_id or 'latest'}/signoff/{metric_id}/sign", actor=actor, comment=comment
        )

    def reject_metric(self, run_id, metric_id, actor, comment=""):
        return self._post(
            f"/runs/{run_id or 'latest'}/signoff/{metric_id}/reject", actor=actor, comment=comment
        )

    def set_signoff_policy(self, actor, required, comment=""):
        return self._post("/admin/signoff/policy", actor=actor, required=list(required), comment=comment)

    def var_setup(self):
        return self._get("/admin/var/setup")

    def set_var_setup(self, actor, measures, comment=""):
        return self._post(
            "/admin/var/setup", actor=actor, measures=[dict(m) for m in measures], comment=comment
        )

    def apply_var_template(self, template, actor, comment=""):
        return self._post("/admin/var/template", actor=actor, template=template, comment=comment)

    def rerun_stage(self, run_id, stage, actor, reason=""):
        return self._post("/admin/rerun", run_id=run_id, stage=stage, actor=actor, reason=reason)

    def _post(self, path: str, **body: Any) -> Any:
        return self._check(self.http.post(path, json=body))

    def trade_extract_options(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/trade-extract/options")

    def trade_extract(self, run_id=None, **filters):
        return self._get(f"/runs/{self._rid(run_id)}/trade-extract", **filters)

    def trade_extract_csv(self, run_id=None, **filters):
        params = {k: v for k, v in filters.items() if v not in (None, "", [], ())}
        r = self.http.get(f"/runs/{self._rid(run_id)}/trade-extract.csv", params=params, timeout=120)
        if r.status_code >= 400:
            self._check(r)
        return r.content

    def limit_hierarchy(self, run_id=None):
        return self._get("/limits/hierarchy", run_id=run_id)

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
        return self._check(
            self.http.post(
                "/analyst/ask",
                json={"question": question, "run_id": run_id, "session_id": session_id},
                timeout=300,
            )
        )

    def commentary(self, run_id=None):
        return self._check(
            self.http.post("/analyst/commentary", params={"run_id": run_id} if run_id else None, timeout=300)
        )

    def analyst_history(self, limit=50):
        return self._get("/analyst/history", limit=limit)

    def analyst_provider(self):
        return self._get("/analyst/provider")

    # --- agents and lab (long-running: generous timeouts) ---
    def _post_long(self, path, **body):
        return self._check(self.http.post(path, json=body, timeout=1800))

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
        try:
            return self._get(f"/runs/{self._rid(run_id)}/reconciliation")
        except NotFoundError as e:
            if e.code == "RECONCILIATION_NOT_FOUND":
                return None
            raise

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
        return self._check(
            self.http.post(
                f"/runs/{self._rid(run_id)}/csa-what-if", json={"netting_set_id": netting_set_id, **terms}
            )
        )

    def fund(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/fund")

    def capital(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/capital")

    def risk_pack_content(self, run_id=None, fmt="html"):
        r = self.http.get(f"/runs/{self._rid(run_id)}/risk-pack/{fmt}", timeout=120)
        if r.status_code >= 400:
            self._check(r)
        return r.content

    def risk_pack(self, run_id=None, out_dir=None, pdf=True):
        return self._check(
            self.http.post(f"/runs/{self._rid(run_id)}/risk-pack", params={"pdf": pdf}, timeout=300)
        )


def make_client(settings, db_path=None) -> RiskClient:
    import os

    url = os.environ.get("NOVERA_API_URL")
    return HttpClient(url) if url else LocalClient(db_path or settings.db_path)  # type: ignore[return-value]
