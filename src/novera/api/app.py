"""FastAPI application. Thin: every endpoint delegates to RiskService.

Contract: every route declares a response model (the OpenAPI schema is committed at
``docs/api/openapi.json``); every error is an RFC 9457 problem document with a stable
``code`` (see ``novera.api.errors``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from novera import __version__
from novera.api import schemas as s
from novera.api.errors import (
    PROBLEM_MEDIA_TYPE,
    ApiError,
    InvalidRequestError,
    NotFoundError,
    Problem,
    problem_from,
    translate,
)
from novera.api.service import RiskService, RiskWriteService
from novera.config import get_settings
from novera.storage.duckdb_repository import DuckDBRepository

settings = get_settings()

PROBLEM_RESPONSES = {
    404: {"model": Problem, "description": "Run, trade, breach or other record not found"},
    409: {"model": Problem, "description": "Workflow rule violated"},
    422: {"model": Problem, "description": "Validation failed"},
}

app = FastAPI(
    title=f"{settings.platform_name} Risk API",
    version=__version__,
    description=settings.platform_tagline,
    responses=PROBLEM_RESPONSES,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --- errors: one problem document for every failure -----------------------------------------
def _problem_response(err: ApiError, request: Request) -> JSONResponse:
    body = problem_from(err, instance=request.url.path).model_dump(mode="json")
    return JSONResponse(body, status_code=err.status, media_type=PROBLEM_MEDIA_TYPE)


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _problem_response(exc, request)


@app.exception_handler(KeyError)
@app.exception_handler(ValueError)
async def _engine_error(request: Request, exc: Exception) -> JSONResponse:
    err = translate(exc)
    if err is None:  # pragma: no cover - translate() covers both classes
        raise exc
    return _problem_response(err, request)


@app.exception_handler(RequestValidationError)
async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()]
    err = InvalidRequestError("; ".join(problems) or "invalid request", context={"errors": exc.errors()})
    return _problem_response(err, request)


@app.exception_handler(HTTPException)
async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    code = {404: "NOT_FOUND", 409: "WORKFLOW_CONFLICT", 422: "VALIDATION_FAILED"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    err = ApiError(str(exc.detail), code=code, status=exc.status_code)
    err.title = {404: "Not found", 409: "Workflow rule violated", 422: "Validation failed"}.get(
        exc.status_code, "Request failed"
    )
    return _problem_response(err, request)


# --- dependencies ----------------------------------------------------------------------------
def service() -> Iterator[RiskService]:
    with DuckDBRepository(settings.db_path, read_only=True) as repo:
        yield RiskService(repo)


def write_service() -> Iterator[RiskWriteService]:
    with DuckDBRepository(settings.db_path) as repo:
        yield RiskWriteService(repo)


# --- request bodies --------------------------------------------------------------------------
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


# --- runs and measures -----------------------------------------------------------------------
@app.get("/health", response_model=s.Health)
def health():
    return {"status": "ok", "platform": settings.platform_name, "version": __version__}


@app.get("/runs", response_model=list[s.RunRecord])
def runs(limit: int = 20, svc: RiskService = Depends(service)):
    return svc.runs(limit)


@app.get("/runs/{run_id}/summary", response_model=s.RunSummary)
def summary(run_id: str, svc: RiskService = Depends(service)):
    return svc.summary(run_id)


@app.get("/runs/{run_id}/positions", response_model=list[s.PositionRow])
def positions(
    run_id: str,
    by: str = "desk_id",
    desk_id: str | None = None,
    book_id: str | None = None,
    business_id: str | None = None,
    asset_class: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.positions(
        run_id, by, desk_id=desk_id, book_id=book_id, business_id=business_id, asset_class=asset_class
    )


@app.get("/runs/{run_id}/var", response_model=list[s.VarRow])
def var(
    run_id: str,
    by: str = "asset_class",
    method: str = "historical_full_revaluation",
    desk_id: str | None = None,
    business_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.var_by(by, run_id, method, desk_id=desk_id, business_id=business_id)


@app.get("/runs/{run_id}/var/summary", response_model=list[s.VarSummaryRow])
def var_summary(run_id: str, svc: RiskService = Depends(service)):
    return svc.var_summary(run_id)


@app.get("/runs/{run_id}/var/scenarios", response_model=list[s.VarScenarioRow])
def var_scenarios(run_id: str, svc: RiskService = Depends(service)):
    return svc.var_scenarios(run_id)


@app.get("/runs/{run_id}/sensitivities", response_model=list[s.SensitivityRow])
def sensitivities(
    run_id: str,
    measure: str = "DV01",
    by: str = "desk_id",
    desk_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.sensitivities(run_id, measure, by, desk_id=desk_id)


@app.get("/runs/{run_id}/stress", response_model=list[s.StressRow])
def stress(
    run_id: str,
    by: str | None = "asset_class",
    desk_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.stress(run_id, by, desk_id=desk_id)


@app.get("/runs/{run_id}/limits", response_model=list[s.LimitRow])
def limits(
    run_id: str,
    status: str | None = Query(None),
    level: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.limits(run_id, status, level)


@app.get("/runs/{run_id}/dq", response_model=list[s.DQFinding])
def dq(run_id: str, svc: RiskService = Depends(service)):
    return svc.dq(run_id)


@app.get("/runs/{run_id}/pnl", response_model=s.PnLExplain)
def pnl(run_id: str, by: str | None = None, desk_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.pnl(run_id, by, desk_id=desk_id)


class ExtractFilters(BaseModel):
    """Query filters of the trade extract; list filters repeat the parameter."""

    business_id: list[str] | None = None
    desk_id: list[str] | None = None
    book_id: list[str] | None = None
    legal_entity_id: list[str] | None = None
    trader_id: list[str] | None = None
    asset_class: list[str] | None = None
    product_type: list[str] | None = None
    currency: list[str] | None = None
    direction: list[str] | None = None
    status: list[str] | None = None
    clearing: list[str] | None = None
    counterparty_id: list[str] | None = None
    netting_set_id: list[str] | None = None
    trade_date_from: str | None = None
    trade_date_to: str | None = None
    maturity_from: str | None = None
    maturity_to: str | None = None
    min_abs_pv: float | None = None
    min_abs_quantity: float | None = None
    q: str | None = Field(default=None, description="Matches trade id, instrument id or description")
    trade_ids: list[str] | None = None


@app.get("/runs/{run_id}/trade-extract/options", response_model=s.TradeExtractOptions)
def trade_extract_options(run_id: str, svc: RiskService = Depends(service)):
    return svc.trade_extract_options(run_id)


@app.get("/runs/{run_id}/trade-extract", response_model=s.TradeExtract)
def trade_extract(
    run_id: str, f: Annotated[ExtractFilters, Query()], svc: RiskService = Depends(service)
):
    return svc.trade_extract(run_id, **f.model_dump(exclude_none=True))


@app.get(
    "/runs/{run_id}/trade-extract.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **PROBLEM_RESPONSES},
    summary="The same extract as a CSV file",
)
def trade_extract_csv(
    run_id: str, f: Annotated[ExtractFilters, Query()], svc: RiskService = Depends(service)
):
    r = svc.resolve(run_id)
    body = svc.trade_extract_csv(r.run_id, **f.model_dump(exclude_none=True))
    name = f"trades_{r.business_date.isoformat()}_{r.run_id}.csv"
    return Response(
        body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@app.get("/runs/{run_id}/trades/{trade_id}", response_model=s.TradeDetail)
def trade(run_id: str, trade_id: str, svc: RiskService = Depends(service)):
    return svc.trade(trade_id, run_id)


@app.get("/audit", response_model=list[s.AuditEvent])
def audit(subject: str | None = None, limit: int = 100, svc: RiskService = Depends(service)):
    return svc.audit(subject, limit)


@app.get("/organisation", response_model=s.Organisation)
def organisation(firm_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.organisation(firm_id)


@app.get("/reference/products", response_model=s.ProductReference)
def product_reference(svc: RiskService = Depends(service)):
    return svc.product_reference()


@app.get("/reference/model-inventory", response_model=s.ModelInventory)
def model_inventory(svc: RiskService = Depends(service)):
    return svc.model_inventory()


@app.get("/reference/measures", response_model=s.MeasureReference)
def measure_reference(svc: RiskService = Depends(service)):
    return svc.measure_reference()


@app.get("/reference/risk-factors", response_model=s.RiskFactorReference)
def risk_factor_reference(svc: RiskService = Depends(service)):
    return svc.risk_factor_reference()


@app.get("/reference/counterparties", response_model=s.CounterpartyReference)
def counterparty_reference(svc: RiskService = Depends(service)):
    return svc.counterparty_reference()


# --- limit management -----------------------------------------------------------------------
@app.get("/limits/hierarchy", response_model=s.LimitHierarchy)
def limit_hierarchy(run_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.limit_hierarchy(run_id)


# --- breach workflow ------------------------------------------------------------------------
@app.get("/breaches", response_model=list[s.BreachRecord])
def breaches(open_only: bool = True, limit_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.breaches(open_only, limit_id)


@app.get("/breaches/{breach_id}", response_model=s.BreachDetail)
def breach(breach_id: str, svc: RiskService = Depends(service)):
    return svc.breach(breach_id)


@app.post("/breaches/{breach_id}/acknowledge", response_model=s.BreachRecord)
def acknowledge(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.acknowledge(breach_id, body.actor, body.comment)


@app.post("/breaches/{breach_id}/escalate", response_model=s.BreachRecord)
def escalate(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.escalate(breach_id, body.actor, body.to, body.comment)


@app.post("/breaches/{breach_id}/comment", response_model=s.BreachRecord)
def comment(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.comment(breach_id, body.actor, body.comment)


@app.post("/breaches/{breach_id}/close", response_model=s.BreachRecord)
def close(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    if not body.reason:
        raise InvalidRequestError("reason is required", context={"field": "reason"})
    return svc.close(breach_id, body.actor, body.reason, body.comment)


@app.get("/increases", response_model=list[s.IncreaseRecord])
def increases(status: str | None = None, limit_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.increases(status, limit_id)


@app.post("/increases", response_model=s.IncreaseRecord)
def request_increase(body: IncreaseBody, svc: RiskWriteService = Depends(write_service)):
    return svc.request_increase(
        body.limit_id,
        body.new_amount,
        body.expires_on,
        body.requested_by,
        body.rationale,
        body.effective_from,
        body.breach_id,
    )


@app.post("/increases/{increase_id}/decide", response_model=s.IncreaseRecord)
def decide_increase(increase_id: str, body: DecisionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.decide_increase(increase_id, body.approver, body.approve, body.comment)


@app.post("/increases/{increase_id}/cancel", response_model=s.IncreaseRecord)
def cancel_increase(increase_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.cancel_increase(increase_id, body.actor)


@app.get("/compare", response_model=s.RunComparison)
def compare(run_a: str, run_b: str, by: str = "asset_class", svc: RiskService = Depends(service)):
    return svc.compare(run_a, run_b, by)


# --- Risk Copilot --------------------------------------------------------------------------------
@app.post("/copilot/ask", response_model=s.CopilotAnswer)
def copilot_ask(body: AskBody):
    from novera.ai import Copilot

    return Copilot(settings.db_path).ask(body.question, body.run_id, session_id=body.session_id).to_dict()


@app.post("/copilot/commentary", response_model=s.CopilotAnswer)
def copilot_commentary(run_id: str | None = None):
    from novera.ai import Copilot

    return Copilot(settings.db_path).commentary(run_id).to_dict()


@app.get("/copilot/history", response_model=list[s.CopilotAnswer])
def copilot_history(limit: int = 50):
    from novera.ai import Copilot

    return Copilot(settings.db_path).history(limit)


@app.get("/copilot/provider", response_model=s.ProviderInfo)
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


@app.post("/agents/investigate-breach", response_model=s.AgentNote)
def agent_investigate(body: InvestigateBody):
    return _ops().investigate_breach(body.breach_id, body.attach)


@app.post("/agents/suggest-scenarios", response_model=s.AgentNote)
def agent_scenarios(body: ScenariosBody):
    return _ops().suggest_scenarios(body.run_id, body.n)


@app.post("/agents/draft-validation", response_model=s.AgentNote)
def agent_validation(body: ValidationBody):
    return _ops().draft_validation(body.run_id, body.records, body.run_tests)


@app.post("/agents/propose-csa", response_model=s.AgentNote)
def agent_propose_csa(body: DocumentBody):
    return _ops().propose_csa(body.path)


@app.post("/agents/approve-csa", response_model=s.NoteDecision)
def agent_approve_csa(body: NoteDecisionBody):
    return _ops().approve_csa(body.note_id, body.actor)


@app.post("/agents/reject-csa", response_model=s.NoteDecision)
def agent_reject_csa(body: NoteDecisionBody):
    return _ops().reject_csa(body.note_id, body.actor, body.reason)


@app.get("/agents/notes", response_model=list[s.AgentNote])
def agent_notes(kind: str | None = None, subject: str | None = None, limit: int = 50):
    return _ops().agent_notes(kind, subject, limit)


@app.get("/lab/catalogue", response_model=s.ProblemCatalogue)
def lab_catalogue():
    return _ops().problem_catalogue()


@app.post("/lab/run", response_model=s.LabRecord)
def lab_run(body: LabBody):
    return _ops().run_lab(body.model_dump())


@app.get("/lab", response_model=list[s.LabRecord])
def lab_list():
    return _ops().labs()


@app.get("/lab/{name}", response_model=s.LabRecord)
def lab_detail(name: str):
    out = _ops().lab(name)
    if out is None:
        raise NotFoundError(f"lab {name} not found", code="LAB_NOT_FOUND", context={"name": name})
    return out


# --- alerts, jobs, reconciliation ------------------------------------------------------------
@app.get("/alerts", response_model=list[s.AlertRecord])
def alerts(
    limit: int = 200,
    status: str | None = None,
    severity: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.alerts(limit, status, severity)


@app.get("/jobs", response_model=list[s.JobRecord])
def jobs(limit: int = 100, svc: RiskService = Depends(service)):
    return svc.jobs(limit)


@app.get("/runs/{run_id}/reconciliation", response_model=s.Reconciliation)
def reconciliation(run_id: str, svc: RiskService = Depends(service)):
    out = svc.reconciliation(run_id)
    if out is None:
        raise NotFoundError(
            "no reconciliation stored for this run",
            code="RECONCILIATION_NOT_FOUND",
            context={"run_id": run_id},
        )
    return out


@app.get("/market-data/provenance", response_model=list[s.ProvenanceRow])
def provenance(svc: RiskService = Depends(service)):
    return svc.provenance()


# --- concentration, liquidity, backtest, risk pack -------------------------------------------------
@app.get("/runs/{run_id}/concentration", response_model=s.ConcentrationReport)
def concentration(run_id: str, svc: RiskService = Depends(service)):
    return svc.concentration(run_id)


@app.get("/runs/{run_id}/liquidity", response_model=s.LiquidityReport)
def liquidity(run_id: str, svc: RiskService = Depends(service)):
    return svc.liquidity(run_id)


@app.get("/runs/{run_id}/lookthrough", response_model=s.LookthroughReport)
def lookthrough(run_id: str, svc: RiskService = Depends(service)):
    return svc.lookthrough(run_id)


@app.get("/runs/{run_id}/market-data-proxies", response_model=s.MarketDataProxies)
def market_data_proxies(run_id: str, svc: RiskService = Depends(service)):
    return svc.market_data_proxies(run_id)


@app.get("/runs/{run_id}/backtest", response_model=s.BacktestReport)
def backtest(run_id: str, svc: RiskService = Depends(service)):
    return svc.backtest(run_id)


@app.post("/runs/{run_id}/risk-pack", response_model=s.RiskPackFiles)
def risk_pack(run_id: str, pdf: bool = True, svc: RiskService = Depends(service)):
    return svc.risk_pack(run_id, str(settings.data_dir / "reports"), pdf)


RISK_PACK_MEDIA = {
    "html": "text/html",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@app.get(
    "/runs/{run_id}/risk-pack/{fmt}",
    response_class=FileResponse,
    responses={200: {"content": {v: {} for v in RISK_PACK_MEDIA.values()}}, **PROBLEM_RESPONSES},
    summary="Download a risk pack file built earlier by POST /runs/{run_id}/risk-pack",
)
def risk_pack_file(run_id: str, fmt: str, svc: RiskService = Depends(service)):
    if fmt not in RISK_PACK_MEDIA:
        raise InvalidRequestError(f"format must be one of {', '.join(RISK_PACK_MEDIA)}", context={"fmt": fmt})
    r = svc.resolve(run_id)
    path = Path(settings.data_dir) / "reports" / f"risk_pack_{r.business_date.isoformat()}_{r.run_id}.{fmt}"
    if not path.exists():
        raise NotFoundError(
            f"no {fmt} risk pack built for run {r.run_id}; POST /runs/{r.run_id}/risk-pack first",
            code="RISK_PACK_NOT_BUILT",
            context={"run_id": r.run_id, "fmt": fmt},
        )
    return FileResponse(path, media_type=RISK_PACK_MEDIA[fmt], filename=path.name)


# --- counterparty risk ------------------------------------------------------------------------------
@app.get("/runs/{run_id}/counterparties", response_model=s.CounterpartyExposures)
def counterparties(run_id: str, svc: RiskService = Depends(service)):
    return svc.counterparties(run_id)


@app.get("/runs/{run_id}/counterparties/{counterparty_id}", response_model=s.CounterpartyDetail)
def counterparty(run_id: str, counterparty_id: str, svc: RiskService = Depends(service)):
    return svc.counterparty(counterparty_id, run_id)


@app.post("/runs/{run_id}/csa-what-if", response_model=s.CSAWhatIf)
def csa_what_if(run_id: str, body: CSAWhatIfBody, svc: RiskService = Depends(service)):
    return svc.csa_what_if(
        body.netting_set_id,
        run_id,
        body.threshold_they_post,
        body.threshold_we_post,
        body.minimum_transfer_amount,
        body.independent_amount,
        body.uncollateralised,
        body.margin_period_days,
    )


@app.get("/runs/{run_id}/capital", response_model=s.CapitalReport)
def capital(run_id: str, svc: RiskService = Depends(service)):
    return svc.capital(run_id)


@app.get("/runs/{run_id}/fund", response_model=s.FundReport)
def fund(run_id: str, svc: RiskService = Depends(service)):
    return svc.fund(run_id)
