"""Client used by the UI. Local mode calls the service in-process; HTTP mode calls the API.
Both expose the same methods, so screens never know which one they talk to."""

from __future__ import annotations

from typing import Any, Protocol

from novera.api.agents_api import AGENT_METHODS
from novera.api.errors import ApiError, NotFoundError, error_from_problem, translate
from novera.api.service import RiskService, RiskWriteService, with_commentary
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
    def geography(self) -> dict[str, Any]: ...
    def rerun_options(self) -> dict[str, Any]: ...
    def run_options(self) -> dict[str, Any]: ...
    def run_full(
        self, reason: str = "", business_date: str | None = None, advance: bool = False
    ) -> dict[str, Any]: ...
    def morning_commentary(self, run_id: str | None = None) -> dict[str, Any]: ...
    def signoff_status(self, run_id: str | None = None) -> dict[str, Any]: ...
    def limit_drafts(self, status: str | None = None) -> list[dict[str, Any]]: ...
    def propose_limit_recalibration(
        self, run_id: str | None = None, limit_types: list[str] | None = None
    ) -> list[dict[str, Any]]: ...
    def decide_limit_draft(self, draft_id: str, approve: bool, comment: str = "") -> dict[str, Any]: ...
    def signoff_policy(self) -> dict[str, Any]: ...
    def signoff_queue(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def sign_metric(self, run_id: str, metric_id: str, comment: str = "") -> dict[str, Any]: ...
    def reject_metric(self, run_id: str, metric_id: str, comment: str = "") -> dict[str, Any]: ...
    def set_signoff_policy(self, required: list[str], comment: str = "") -> dict[str, Any]: ...
    def var_setup(self) -> dict[str, Any]: ...
    def set_var_setup(self, measures: list[dict[str, Any]], comment: str = "") -> dict[str, Any]: ...
    def apply_var_template(self, template: str, comment: str = "") -> dict[str, Any]: ...
    def stress_setup(self) -> dict[str, Any]: ...
    def propose_stress_scenario(self, definition: dict[str, Any], note: str = "") -> dict[str, Any]: ...
    def decide_stress_scenario(
        self, scenario_id: str, version: int, approve: bool, note: str = ""
    ) -> dict[str, Any]: ...
    def review_stress_scenario(self, scenario_id: str, note: str = "") -> dict[str, Any]: ...
    def seed_stress_library(self, note: str = "") -> dict[str, Any]: ...
    def stress_plausibility(self) -> dict[str, Any]: ...
    def upgrade_stress_built_ins(self, note: str = "") -> list[dict[str, Any]]: ...
    def draft_stress_crises(self) -> list[dict[str, Any]]: ...
    def rerun_stage(self, run_id: str, stage: str, reason: str = "") -> dict[str, Any]: ...
    def me(self) -> dict[str, Any]: ...
    def users(self) -> list[dict[str, Any]]: ...


WRITE_METHODS = {
    "acknowledge",
    "escalate",
    "comment",
    "close",
    "request_increase",
    "decide_increase",
    "cancel_increase",
    "rerun_stage",
    "run_full",
    "sign_metric",
    "reject_metric",
    "set_signoff_policy",
    "set_var_setup",
    "apply_var_template",
    "propose_stress_scenario",
    "decide_stress_scenario",
    "review_stress_scenario",
    "seed_stress_library",
    "upgrade_stress_built_ins",
    "draft_stress_crises",
    "propose_limit_recalibration",
    "decide_limit_draft",
}
ANALYST_METHODS = {"ask", "commentary", "analyst_history", "analyst_provider"}
SIGNED_IN = {"ask", "commentary", "risk_pack"}  # any user, but a signed-in one (ADR 0006)
AGENT_WRITES = {
    "investigate_breach",
    "suggest_scenarios",
    "draft_validation",
    "propose_csa",
    "approve_csa",
    "reject_csa",
    "run_lab",
}
PACK_METHODS = {"risk_pack"}


class LocalClient:
    """Opens a short-lived connection per call: read-only for reads so it coexists with a
    writer (the EOD run), writable only for workflow actions. Writes act as ``user_id``, the
    dashboard's signed-in user (its user picker stands in for single sign-on, ADR 0006)."""

    def __init__(self, db_path, user_id: str | None = None) -> None:
        self.db_path = db_path
        self.user_id = user_id

    def _user(self):
        from novera.api.errors import UnauthenticatedError

        if self.user_id is None:
            raise UnauthenticatedError("choose who you are acting as before a write")
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            user = repo.load_user(self.user_id)
        if user is None or not user.active:
            raise UnauthenticatedError(f"no active user {self.user_id}")
        return user

    def me(self) -> dict[str, Any]:
        return self._user().model_dump(mode="json")

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
        if name in SIGNED_IN:
            self._user()
        if name in ANALYST_METHODS:
            from novera.ai import Analyst, make_provider

            if name == "analyst_provider":
                p = make_provider()
                return {"provider": p.name, "model": p.model, "chain": getattr(p, "chain", [])}
            c = Analyst(self.db_path)
            if name == "analyst_history":
                return c.history(*a, **kw)
            return getattr(c, name)(*a, **kw).to_dict()
        if name in AGENT_METHODS:
            from novera.api.agents_api import AgentOps

            user = self._user() if name in AGENT_WRITES else None
            return getattr(AgentOps(self.db_path, user), name)(*a, **kw)
        if name in WRITE_METHODS:
            user = self._user()  # read before the writable connection opens
            with DuckDBRepository(self.db_path) as repo:
                out = getattr(RiskWriteService(repo, user), name)(*a, **kw)
            if name == "run_full":  # draft once the run's connection is closed (decision round 38)
                out = with_commentary(str(self.db_path), out)
            return out
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
    """Sends ``token`` (a personal access token, ADR 0006) on every request."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        import httpx

        self.base = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=self.base, timeout=30, headers=headers)

    def me(self):
        return self._get("/me")

    def users(self):
        return self._get("/users")

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

    def geography(self):
        return self._get("/reference/geography")

    def rerun_options(self):
        return self._get("/admin/rerun/options")

    def run_options(self):
        return self._get("/admin/run/options")

    def run_full(self, reason="", business_date=None, advance=False):
        return self._post_long("/admin/run", reason=reason, business_date=business_date, advance=advance)

    def morning_commentary(self, run_id=None):
        return self._get(f"/runs/{run_id or 'latest'}/commentary")

    def signoff_status(self, run_id=None):
        return self._get(f"/runs/{run_id or 'latest'}/signoff")

    def signoff_policy(self):
        return self._get("/admin/signoff/policy")

    def signoff_queue(self, limit=20):
        return self._get("/signoff/queue", limit=limit)

    def sign_metric(self, run_id, metric_id, comment=""):
        return self._post(f"/runs/{run_id or 'latest'}/signoff/{metric_id}/sign", comment=comment)

    def reject_metric(self, run_id, metric_id, comment=""):
        return self._post(f"/runs/{run_id or 'latest'}/signoff/{metric_id}/reject", comment=comment)

    def set_signoff_policy(self, required, comment=""):
        return self._post("/admin/signoff/policy", required=list(required), comment=comment)

    def var_setup(self):
        return self._get("/admin/var/setup")

    def set_var_setup(self, measures, comment=""):
        return self._post("/admin/var/setup", measures=[dict(m) for m in measures], comment=comment)

    def apply_var_template(self, template, comment=""):
        return self._post("/admin/var/template", template=template, comment=comment)

    def stress_setup(self):
        return self._get("/admin/stress/setup")

    def propose_stress_scenario(self, definition, note=""):
        return self._post("/admin/stress/proposals", definition=dict(definition), note=note)

    def decide_stress_scenario(self, scenario_id, version, approve, note=""):
        return self._post(
            "/admin/stress/decisions",
            scenario_id=scenario_id,
            version=int(version),
            approve=bool(approve),
            note=note,
        )

    def review_stress_scenario(self, scenario_id, note=""):
        return self._post("/admin/stress/reviews", scenario_id=scenario_id, note=note)

    def seed_stress_library(self, note=""):
        return self._post("/admin/stress/seed", note=note)

    def stress_plausibility(self):
        return self._get("/admin/stress/plausibility")

    def upgrade_stress_built_ins(self, note=""):
        return self._post("/admin/stress/upgrade", note=note)

    def draft_stress_crises(self):
        return self._post("/admin/stress/crises")

    def rerun_stage(self, run_id, stage, reason=""):
        return self._post("/admin/rerun", run_id=run_id, stage=stage, reason=reason)

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

    def acknowledge(self, breach_id, comment=""):
        return self._post(f"/breaches/{breach_id}/acknowledge", comment=comment)

    def escalate(self, breach_id, to=None, comment=""):
        return self._post(f"/breaches/{breach_id}/escalate", to=to, comment=comment)

    def comment(self, breach_id, text):
        return self._post(f"/breaches/{breach_id}/comment", comment=text)

    def close(self, breach_id, reason, comment=""):
        return self._post(f"/breaches/{breach_id}/close", reason=reason, comment=comment)

    def limit_drafts(self, status=None):
        return self._get("/limits/drafts", status=status)

    def propose_limit_recalibration(self, run_id=None, limit_types=None):
        return self._post("/limits/recalibrate", run_id=run_id, limit_types=limit_types or [])

    def decide_limit_draft(self, draft_id, approve, comment=""):
        return self._post(f"/limits/drafts/{draft_id}/decide", approve=approve, comment=comment)

    def increases(self, status=None, limit_id=None):
        return self._get("/increases", status=status, limit_id=limit_id)

    def request_increase(
        self, limit_id, new_amount, expires_on, rationale, effective_from=None, breach_id=None
    ):
        return self._post(
            "/increases",
            limit_id=limit_id,
            new_amount=new_amount,
            expires_on=expires_on,
            rationale=rationale,
            effective_from=effective_from,
            breach_id=breach_id,
        )

    def decide_increase(self, increase_id, approve, comment=""):
        return self._post(f"/increases/{increase_id}/decide", approve=approve, comment=comment)

    def cancel_increase(self, increase_id):
        return self._post(f"/increases/{increase_id}/cancel")

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

    def approve_csa(self, note_id):
        return self._post_long("/agents/approve-csa", note_id=note_id)

    def reject_csa(self, note_id, reason=""):
        return self._post_long("/agents/reject-csa", note_id=note_id, reason=reason)

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


def make_client(settings, db_path=None, user_id: str | None = None) -> RiskClient:
    """HTTP when NOVERA_API_URL is set (sending NOVERA_API_TOKEN), in-process otherwise acting as
    ``user_id``."""
    import os

    url = os.environ.get("NOVERA_API_URL")
    if url:
        return HttpClient(url, settings.api_token)  # type: ignore[return-value]
    return LocalClient(db_path or settings.db_path, user_id)  # type: ignore[return-value]
