"""FastAPI application. Thin: every endpoint delegates to RiskService."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from novera import __version__
from novera.api.service import RiskService, RiskWriteService, RunNotFoundError
from novera.config import get_settings
from novera.limits import WorkflowError
from novera.storage.duckdb_repository import DuckDBRepository

settings = get_settings()
app = FastAPI(
    title=f"{settings.platform_name} Risk API", version=__version__, description=settings.platform_tagline
)


def service() -> Iterator[RiskService]:
    with DuckDBRepository(settings.db_path, read_only=True) as repo:
        yield RiskService(repo)


def write_service() -> Iterator[RiskWriteService]:
    with DuckDBRepository(settings.db_path) as repo:
        yield RiskWriteService(repo)


def _guard(fn, *a, **kw) -> Any:
    try:
        return fn(*a, **kw)
    except RunNotFoundError as e:
        raise HTTPException(404, f"run {e.run_id} not found") from e
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except WorkflowError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class ActionBody(BaseModel):
    actor: str = Field(min_length=1)
    comment: str = ""
    to: str | None = None
    reason: str | None = None


class IncreaseBody(BaseModel):
    limit_id: str
    new_amount: float = Field(gt=0)
    expires_on: str
    requested_by: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    effective_from: str | None = None
    breach_id: str | None = None


class DecisionBody(BaseModel):
    approver: str = Field(min_length=1)
    approve: bool
    comment: str = ""


class CSAWhatIfBody(BaseModel):
    netting_set_id: str
    threshold_they_post: float | None = None
    threshold_we_post: float | None = None
    minimum_transfer_amount: float | None = None
    independent_amount: float | None = None
    uncollateralised: bool = False
    margin_period_days: int = 10


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    run_id: str | None = None
    session_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "platform": settings.platform_name, "version": __version__}


@app.get("/runs")
def runs(limit: int = 20, svc: RiskService = Depends(service)):
    return svc.runs(limit)


@app.get("/runs/{run_id}/summary")
def summary(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.summary, run_id)


@app.get("/runs/{run_id}/positions")
def positions(
    run_id: str,
    by: str = "desk_id",
    desk_id: str | None = None,
    book_id: str | None = None,
    business_id: str | None = None,
    asset_class: str | None = None,
    svc: RiskService = Depends(service),
):
    return _guard(
        svc.positions,
        run_id,
        by,
        desk_id=desk_id,
        book_id=book_id,
        business_id=business_id,
        asset_class=asset_class,
    )


@app.get("/runs/{run_id}/var")
def var(
    run_id: str,
    by: str = "asset_class",
    method: str = "historical_full_revaluation",
    desk_id: str | None = None,
    business_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return _guard(svc.var_by, by, run_id, method, desk_id=desk_id, business_id=business_id)


@app.get("/runs/{run_id}/var/summary")
def var_summary(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.var_summary, run_id)


@app.get("/runs/{run_id}/var/scenarios")
def var_scenarios(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.var_scenarios, run_id)


@app.get("/runs/{run_id}/sensitivities")
def sensitivities(
    run_id: str,
    measure: str = "DV01",
    by: str = "desk_id",
    desk_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return _guard(svc.sensitivities, run_id, measure, by, desk_id=desk_id)


@app.get("/runs/{run_id}/stress")
def stress(
    run_id: str,
    by: str | None = "asset_class",
    desk_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return _guard(svc.stress, run_id, by, desk_id=desk_id)


@app.get("/runs/{run_id}/limits")
def limits(
    run_id: str,
    status: str | None = Query(None),
    level: str | None = None,
    svc: RiskService = Depends(service),
):
    return _guard(svc.limits, run_id, status, level)


@app.get("/runs/{run_id}/dq")
def dq(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.dq, run_id)


@app.get("/runs/{run_id}/pnl")
def pnl(run_id: str, by: str | None = None, desk_id: str | None = None, svc: RiskService = Depends(service)):
    return _guard(svc.pnl, run_id, by, desk_id=desk_id)


@app.get("/runs/{run_id}/trades/{trade_id}")
def trade(run_id: str, trade_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.trade, trade_id, run_id)


@app.get("/audit")
def audit(subject: str | None = None, limit: int = 100, svc: RiskService = Depends(service)):
    return svc.audit(subject, limit)


@app.get("/organisation")
def organisation(svc: RiskService = Depends(service)):
    return svc.organisation()


# --- breach workflow ------------------------------------------------------------------------
@app.get("/breaches")
def breaches(open_only: bool = True, limit_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.breaches(open_only, limit_id)


@app.get("/breaches/{breach_id}")
def breach(breach_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.breach, breach_id)


@app.post("/breaches/{breach_id}/acknowledge")
def acknowledge(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(svc.acknowledge, breach_id, body.actor, body.comment)


@app.post("/breaches/{breach_id}/escalate")
def escalate(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(svc.escalate, breach_id, body.actor, body.to, body.comment)


@app.post("/breaches/{breach_id}/comment")
def comment(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(svc.comment, breach_id, body.actor, body.comment)


@app.post("/breaches/{breach_id}/close")
def close(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    if not body.reason:
        raise HTTPException(422, "reason is required")
    return _guard(svc.close, breach_id, body.actor, body.reason, body.comment)


@app.get("/increases")
def increases(status: str | None = None, limit_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.increases(status, limit_id)


@app.post("/increases")
def request_increase(body: IncreaseBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(
        svc.request_increase,
        body.limit_id,
        body.new_amount,
        body.expires_on,
        body.requested_by,
        body.rationale,
        body.effective_from,
        body.breach_id,
    )


@app.post("/increases/{increase_id}/decide")
def decide_increase(increase_id: str, body: DecisionBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(svc.decide_increase, increase_id, body.approver, body.approve, body.comment)


@app.post("/increases/{increase_id}/cancel")
def cancel_increase(increase_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return _guard(svc.cancel_increase, increase_id, body.actor)


@app.get("/compare")
def compare(run_a: str, run_b: str, by: str = "asset_class", svc: RiskService = Depends(service)):
    return _guard(svc.compare, run_a, run_b, by)


# --- Risk Copilot --------------------------------------------------------------------------------
@app.post("/copilot/ask")
def copilot_ask(body: AskBody):
    from novera.ai import Copilot

    return Copilot(settings.db_path).ask(body.question, body.run_id, session_id=body.session_id).to_dict()


@app.post("/copilot/commentary")
def copilot_commentary(run_id: str | None = None):
    from novera.ai import Copilot

    return Copilot(settings.db_path).commentary(run_id).to_dict()


@app.get("/copilot/history")
def copilot_history(limit: int = 50):
    from novera.ai import Copilot

    return Copilot(settings.db_path).history(limit)


@app.get("/copilot/provider")
def copilot_provider():
    from novera.ai import make_provider

    p = make_provider(settings)
    return {"provider": p.name, "model": p.model}


# --- agents and Portfolio Lab ------------------------------------------------------------------
class InvestigateBody(BaseModel):
    breach_id: str
    attach: bool = True


class ScenariosBody(BaseModel):
    run_id: str | None = None
    n: int = Field(default=4, ge=1, le=8)


class ValidationBody(BaseModel):
    run_id: str | None = None
    records: list[str] | None = None
    run_tests: bool = False


class DocumentBody(BaseModel):
    path: str


class NoteDecisionBody(BaseModel):
    note_id: str
    actor: str = Field(min_length=1)
    reason: str = ""


class LabBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    template: str = "bank"
    problems: list[str] = []
    market_problems: list[str] = []
    scale: float = 1.0
    n_trades: int = Field(default=600, ge=50, le=5000)
    seed: int = 42
    business_date: str = "2026-09-11"
    years: float = 2.0
    counterparty: bool = False
    regulatory: bool = False


def _ops():
    from novera.api.agents_api import AgentOps

    return AgentOps(settings.db_path)


@app.post("/agents/investigate-breach")
def agent_investigate(body: InvestigateBody):
    return _guard(_ops().investigate_breach, body.breach_id, body.attach)


@app.post("/agents/suggest-scenarios")
def agent_scenarios(body: ScenariosBody):
    return _guard(_ops().suggest_scenarios, body.run_id, body.n)


@app.post("/agents/draft-validation")
def agent_validation(body: ValidationBody):
    return _guard(_ops().draft_validation, body.run_id, body.records, body.run_tests)


@app.post("/agents/propose-csa")
def agent_propose_csa(body: DocumentBody):
    return _guard(_ops().propose_csa, body.path)


@app.post("/agents/approve-csa")
def agent_approve_csa(body: NoteDecisionBody):
    return _guard(_ops().approve_csa, body.note_id, body.actor)


@app.post("/agents/reject-csa")
def agent_reject_csa(body: NoteDecisionBody):
    return _guard(_ops().reject_csa, body.note_id, body.actor, body.reason)


@app.get("/agents/notes")
def agent_notes(kind: str | None = None, subject: str | None = None, limit: int = 50):
    return _ops().agent_notes(kind, subject, limit)


@app.get("/lab/catalogue")
def lab_catalogue():
    return _ops().problem_catalogue()


@app.post("/lab/run")
def lab_run(body: LabBody):
    return _guard(_ops().run_lab, body.model_dump())


@app.get("/lab")
def lab_list():
    return _ops().labs()


@app.get("/lab/{name}")
def lab_detail(name: str):
    out = _ops().lab(name)
    if out is None:
        raise HTTPException(404, f"lab {name} not found")
    return out


# --- alerts, jobs, reconciliation ------------------------------------------------------------
@app.get("/alerts")
def alerts(
    limit: int = 200,
    status: str | None = None,
    severity: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.alerts(limit, status, severity)


@app.get("/jobs")
def jobs(limit: int = 100, svc: RiskService = Depends(service)):
    return svc.jobs(limit)


@app.get("/runs/{run_id}/reconciliation")
def reconciliation(run_id: str, svc: RiskService = Depends(service)):
    out = _guard(svc.reconciliation, run_id)
    if out is None:
        raise HTTPException(404, "no reconciliation stored for this run")
    return out


@app.get("/market-data/provenance")
def provenance(svc: RiskService = Depends(service)):
    return svc.provenance()


# --- concentration, liquidity, backtest, risk pack -------------------------------------------------
@app.get("/runs/{run_id}/concentration")
def concentration(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.concentration, run_id)


@app.get("/runs/{run_id}/liquidity")
def liquidity(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.liquidity, run_id)


@app.get("/runs/{run_id}/lookthrough")
def lookthrough(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.lookthrough, run_id)


@app.get("/runs/{run_id}/market-data-proxies")
def market_data_proxies(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.market_data_proxies, run_id)


@app.get("/runs/{run_id}/backtest")
def backtest(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.backtest, run_id)


@app.post("/runs/{run_id}/risk-pack")
def risk_pack(run_id: str, pdf: bool = True, svc: RiskService = Depends(service)):
    return _guard(svc.risk_pack, run_id, str(settings.data_dir / "reports"), pdf)


# --- counterparty risk ------------------------------------------------------------------------------
@app.get("/runs/{run_id}/counterparties")
def counterparties(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.counterparties, run_id)


@app.get("/runs/{run_id}/counterparties/{counterparty_id}")
def counterparty(run_id: str, counterparty_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.counterparty, counterparty_id, run_id)


@app.post("/runs/{run_id}/csa-what-if")
def csa_what_if(run_id: str, body: CSAWhatIfBody, svc: RiskService = Depends(service)):
    return _guard(
        svc.csa_what_if,
        body.netting_set_id,
        run_id,
        body.threshold_they_post,
        body.threshold_we_post,
        body.minimum_transfer_amount,
        body.independent_amount,
        body.uncollateralised,
        body.margin_period_days,
    )


@app.get("/runs/{run_id}/capital")
def capital(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.capital, run_id)


@app.get("/runs/{run_id}/fund")
def fund(run_id: str, svc: RiskService = Depends(service)):
    return _guard(svc.fund, run_id)
