"""FastAPI application. Thin: every endpoint delegates to RiskService.

Contract: every route declares a response model (the OpenAPI schema is committed at
``docs/api/openapi.json``); every error is an RFC 9457 problem document with a stable
``code`` (see ``novera.api.errors``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
    UnauthenticatedError,
    problem_from,
    translate,
)
from novera.api.service import RiskService, RiskWriteService, with_commentary
from novera.config import get_settings
from novera.security import User, user_for
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


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    """The user a personal access token belongs to (ADR 0006); 401 without a valid one."""
    with DuckDBRepository(settings.db_path, read_only=True) as repo:
        user = user_for(repo, _bearer(authorization))
    if user is None:
        raise UnauthenticatedError(
            "send a personal access token: Authorization: Bearer <token> (novera users token <user>)"
        )
    return user


def read_guard(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    """Reads are open on the local API unless NOVERA_AUTH_REQUIRED_FOR_READS is set."""
    if settings.auth_required_for_reads and request.url.path not in ("/health", "/docs", "/openapi.json"):
        current_user(authorization)


def write_service(user: User = Depends(current_user)) -> Iterator[RiskWriteService]:
    with DuckDBRepository(settings.db_path) as repo:
        yield RiskWriteService(repo, user)


app.router.dependencies.append(Depends(read_guard))


# --- request bodies --------------------------------------------------------------------------
class PolicyBody(BaseModel):
    required: list[str]
    comment: str = ""


class RunBody(BaseModel):
    reason: str = ""
    business_date: str | None = None
    advance: bool = False


class RerunBody(BaseModel):
    run_id: str = "latest"
    stage: str
    reason: str = ""


class VarSetupBody(BaseModel):
    measures: list[s.VarMeasureSpec]
    comment: str = ""


class StressProposalBody(BaseModel):
    definition: s.StressDefinitionSpec
    note: str = ""


class StressDecisionBody(BaseModel):
    scenario_id: str
    version: int
    approve: bool
    note: str = ""


class StressReviewBody(BaseModel):
    scenario_id: str
    note: str = ""


class StressSeedBody(BaseModel):
    note: str = ""


class VarTemplateBody(BaseModel):
    template: str
    comment: str = ""


class ActionBody(BaseModel):
    comment: str = ""
    to: str | None = None
    reason: str | None = None


class IncreaseBody(BaseModel):
    limit_id: str
    new_amount: float = Field(gt=0)
    expires_on: str
    rationale: str = Field(min_length=1)
    effective_from: str | None = None
    breach_id: str | None = None


class DecisionBody(BaseModel):
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


@app.get("/me", response_model=s.UserRecord)
def me(user: User = Depends(current_user)):
    """The user the token belongs to (ADR 0006)."""
    return user.model_dump(mode="json")


@app.get("/users", response_model=list[s.UserRecord])
def users(svc: RiskService = Depends(service)):
    """The role accounts; tokens are never listed here."""
    return svc.users()


@app.get("/runs", response_model=list[s.RunRecord])
def runs(limit: int = 20, svc: RiskService = Depends(service)):
    return svc.runs(limit)


@app.get("/runs/{run_id}/summary", response_model=s.RunSummary)
def summary(run_id: str, svc: RiskService = Depends(service)):
    return svc.summary(run_id)


def geo_filters(
    region: str | None = None,
    country: str | None = None,
    risk_currency: str | None = None,
    currency_pair: str | None = None,
) -> dict[str, str | None]:
    """Geography filters (decision round 44): region and country by name, e.g. "South Asia"."""
    return {
        "region": region,
        "country": country,
        "risk_currency": risk_currency,
        "currency_pair": currency_pair,
    }


@app.get("/runs/{run_id}/positions", response_model=list[s.PositionRow])
def positions(
    run_id: str,
    by: str = "desk_id",
    desk_id: str | None = None,
    book_id: str | None = None,
    business_id: str | None = None,
    asset_class: str | None = None,
    geo: dict = Depends(geo_filters),
    svc: RiskService = Depends(service),
):
    return svc.positions(
        run_id, by, desk_id=desk_id, book_id=book_id, business_id=business_id, asset_class=asset_class, **geo
    )


@app.get("/runs/{run_id}/var", response_model=list[s.VarRow])
def var(
    run_id: str,
    by: str = "asset_class",
    method: str = "historical_full_revaluation",
    measure_id: str | None = None,
    desk_id: str | None = None,
    business_id: str | None = None,
    geo: dict = Depends(geo_filters),
    svc: RiskService = Depends(service),
):
    return svc.var_by(by, run_id, method, measure_id, desk_id=desk_id, business_id=business_id, **geo)


@app.get("/runs/{run_id}/var/summary", response_model=list[s.VarSummaryRow])
def var_summary(run_id: str, svc: RiskService = Depends(service)):
    return svc.var_summary(run_id)


@app.get("/runs/{run_id}/var/measure-scenarios", response_model=list[s.VarMeasureScenarioRow])
def var_measure_scenarios(run_id: str, measure_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.var_measure_scenarios(run_id, measure_id)


@app.get("/runs/{run_id}/var/scenarios", response_model=list[s.VarScenarioRow])
def var_scenarios(run_id: str, svc: RiskService = Depends(service)):
    return svc.var_scenarios(run_id)


@app.get("/runs/{run_id}/sensitivities", response_model=list[s.SensitivityRow])
def sensitivities(
    run_id: str,
    measure: str = "DV01",
    by: str = "desk_id",
    desk_id: str | None = None,
    geo: dict = Depends(geo_filters),
    svc: RiskService = Depends(service),
):
    return svc.sensitivities(run_id, measure, by, desk_id=desk_id, **geo)


@app.get("/runs/{run_id}/stress", response_model=list[s.StressRow])
def stress(
    run_id: str,
    by: str | None = "asset_class",
    desk_id: str | None = None,
    geo: dict = Depends(geo_filters),
    svc: RiskService = Depends(service),
):
    return svc.stress(run_id, by, desk_id=desk_id, **geo)


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
def pnl(
    run_id: str,
    by: str | None = None,
    desk_id: str | None = None,
    geo: dict = Depends(geo_filters),
    svc: RiskService = Depends(service),
):
    return svc.pnl(run_id, by, desk_id=desk_id, **geo)


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
def trade_extract(run_id: str, f: Annotated[ExtractFilters, Query()], svc: RiskService = Depends(service)):
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


@app.get("/reference/market-data-sources", response_model=s.MarketDataSources)
def market_data_sources(svc: RiskService = Depends(service)):
    return svc.market_data_sources()


@app.get("/reference/geography", response_model=s.GeographyReference)
def geography(svc: RiskService = Depends(service)):
    """Regions, countries, currencies with their liquidity score, and the currency pairs of the
    platform with the currency each attaches to (decision round 44)."""
    return svc.geography()


@app.get("/runs/{run_id}/factor-stress", response_model=s.FactorStress)
def factor_stress(
    run_id: str, desk_id: str | None = None, top: int = 25, svc: RiskService = Depends(service)
):
    return svc.factor_stress(run_id, desk_id, top)


@app.get("/runs/{run_id}/stress-explain", response_model=s.StressExplain)
def stress_explain(
    run_id: str,
    scenario_id: str | None = None,
    desk_id: str | None = None,
    svc: RiskService = Depends(service),
):
    return svc.stress_explain(run_id, scenario_id, desk_id)


@app.get("/reference/stress-library", response_model=s.StressLibrary)
def stress_library(svc: RiskService = Depends(service)):
    return svc.stress_library()


@app.get("/reference/counterparties", response_model=s.CounterpartyReference)
def counterparty_reference(svc: RiskService = Depends(service)):
    return svc.counterparty_reference()


@app.get("/runs/{run_id}/commentary", response_model=s.MorningCommentary)
def morning_commentary(run_id: str, svc: RiskService = Depends(service)):
    return svc.morning_commentary(run_id)


# --- sign-off (OPS-003) ---------------------------------------------------------------------
@app.get("/runs/{run_id}/signoff", response_model=s.SignoffStatus)
def signoff_status(run_id: str, svc: RiskService = Depends(service)):
    return svc.signoff_status(run_id)


@app.post("/runs/{run_id}/signoff/{metric_id}/sign", response_model=s.SignoffStatus)
def sign_metric(
    run_id: str, metric_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)
):
    return svc.sign_metric(run_id, metric_id, body.comment)


@app.post("/runs/{run_id}/signoff/{metric_id}/reject", response_model=s.SignoffStatus)
def reject_metric(
    run_id: str, metric_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)
):
    return svc.reject_metric(run_id, metric_id, body.comment)


@app.get("/signoff/queue", response_model=list[s.SignoffQueueRow])
def signoff_queue(limit: int = 20, svc: RiskService = Depends(service)):
    return svc.signoff_queue(limit)


@app.get("/admin/signoff/policy", response_model=s.SignoffPolicy)
def signoff_policy(svc: RiskService = Depends(service)):
    return svc.signoff_policy()


@app.post("/admin/signoff/policy", response_model=s.SignoffPolicy)
def set_signoff_policy(body: PolicyBody, svc: RiskWriteService = Depends(write_service)):
    return svc.set_signoff_policy(body.required, body.comment)


# --- administration -------------------------------------------------------------------------
@app.get("/admin/var/setup", response_model=s.VarSetup)
def var_setup(svc: RiskService = Depends(service)):
    return svc.var_setup()


@app.post("/admin/var/setup", response_model=s.VarSetup)
def set_var_setup(body: VarSetupBody, svc: RiskWriteService = Depends(write_service)):
    return svc.set_var_setup([m.model_dump() for m in body.measures], body.comment)


@app.get("/admin/stress/setup", response_model=s.StressSetup)
def stress_setup(svc: RiskService = Depends(service)):
    return svc.stress_setup()


@app.get("/admin/stress/plausibility", response_model=s.StressPlausibility)
def stress_plausibility(svc: RiskService = Depends(service)):
    return svc.stress_plausibility()


@app.post("/admin/stress/upgrade", response_model=list[s.StressScenarioVersion])
def upgrade_stress_built_ins(body: StressSeedBody, svc: RiskWriteService = Depends(write_service)):
    return svc.upgrade_stress_built_ins(body.note)


@app.post("/admin/stress/crises", response_model=list[s.StressScenarioVersion])
def draft_stress_crises(svc: RiskWriteService = Depends(write_service)):
    return svc.draft_stress_crises()


@app.post("/admin/stress/narratives", response_model=list[s.StressScenarioVersion])
def draft_stress_narratives(svc: RiskWriteService = Depends(write_service)):
    return svc.draft_stress_narratives()


@app.post("/admin/stress/proposals", response_model=s.StressScenarioVersion)
def propose_stress_scenario(body: StressProposalBody, svc: RiskWriteService = Depends(write_service)):
    return svc.propose_stress_scenario(body.definition.model_dump(), body.note)


@app.post("/admin/stress/decisions", response_model=s.StressScenarioVersion)
def decide_stress_scenario(body: StressDecisionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.decide_stress_scenario(body.scenario_id, body.version, body.approve, body.note)


@app.post("/admin/stress/reviews", response_model=s.StressScenarioVersion)
def review_stress_scenario(body: StressReviewBody, svc: RiskWriteService = Depends(write_service)):
    return svc.review_stress_scenario(body.scenario_id, body.note)


@app.post("/admin/stress/seed", response_model=s.StressSetup)
def seed_stress_library(body: StressSeedBody, svc: RiskWriteService = Depends(write_service)):
    return svc.seed_stress_library(body.note)


@app.post("/admin/var/template", response_model=s.VarSetup)
def apply_var_template(body: VarTemplateBody, svc: RiskWriteService = Depends(write_service)):
    return svc.apply_var_template(body.template, body.comment)


@app.get("/admin/run/options", response_model=s.RunOptions)
def run_options(svc: RiskService = Depends(service)):
    return svc.run_options()


@app.post("/admin/run", response_model=s.ManualRun)
def run_full(body: RunBody, user: User = Depends(current_user)):
    # Own connection, closed before the morning commentary opens the Analyst's (decision round 38).
    with DuckDBRepository(settings.db_path) as repo:
        out = RiskWriteService(repo, user).run_full(body.reason, body.business_date, body.advance)
    return with_commentary(str(settings.db_path), out)


@app.get("/admin/rerun/options", response_model=s.RerunOptions)
def rerun_options(svc: RiskService = Depends(service)):
    return svc.rerun_options()


@app.post("/admin/rerun", response_model=s.RerunRecord)
def rerun_stage(body: RerunBody, svc: RiskWriteService = Depends(write_service)):
    return svc.rerun_stage(body.run_id, body.stage, body.reason)


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
    return svc.acknowledge(breach_id, body.comment)


@app.post("/breaches/{breach_id}/escalate", response_model=s.BreachRecord)
def escalate(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.escalate(breach_id, body.to, body.comment)


@app.post("/breaches/{breach_id}/comment", response_model=s.BreachRecord)
def comment(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.comment(breach_id, body.comment)


@app.post("/breaches/{breach_id}/close", response_model=s.BreachRecord)
def close(breach_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    if not body.reason:
        raise InvalidRequestError("reason is required", context={"field": "reason"})
    return svc.close(breach_id, body.reason, body.comment)


@app.get("/limits/drafts", response_model=list[s.LimitDraft])
def limit_drafts(status: str | None = None, svc: RiskService = Depends(service)):
    return svc.limit_drafts(status)


class RecalibrateBody(BaseModel):
    run_id: str | None = None
    limit_types: list[str] = []


@app.post("/limits/recalibrate", response_model=list[s.LimitDraft])
def propose_limit_recalibration(body: RecalibrateBody, svc: RiskWriteService = Depends(write_service)):
    return svc.propose_limit_recalibration(body.run_id, body.limit_types)


@app.post("/limits/drafts/{draft_id}/decide", response_model=s.LimitDraft)
def decide_limit_draft(draft_id: str, body: DecisionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.decide_limit_draft(draft_id, body.approve, body.comment)


@app.get("/increases", response_model=list[s.IncreaseRecord])
def increases(status: str | None = None, limit_id: str | None = None, svc: RiskService = Depends(service)):
    return svc.increases(status, limit_id)


@app.post("/increases", response_model=s.IncreaseRecord)
def request_increase(body: IncreaseBody, svc: RiskWriteService = Depends(write_service)):
    return svc.request_increase(
        body.limit_id,
        body.new_amount,
        body.expires_on,
        body.rationale,
        body.effective_from,
        body.breach_id,
    )


@app.post("/increases/{increase_id}/decide", response_model=s.IncreaseRecord)
def decide_increase(increase_id: str, body: DecisionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.decide_increase(increase_id, body.approve, body.comment)


@app.post("/increases/{increase_id}/cancel", response_model=s.IncreaseRecord)
def cancel_increase(increase_id: str, body: ActionBody, svc: RiskWriteService = Depends(write_service)):
    return svc.cancel_increase(increase_id)


@app.get("/compare", response_model=s.RunComparison)
def compare(run_a: str, run_b: str, by: str = "asset_class", svc: RiskService = Depends(service)):
    return svc.compare(run_a, run_b, by)


# --- Analyst --------------------------------------------------------------------------------
@app.post("/analyst/ask", response_model=s.AnalystAnswer)
def analyst_ask(body: AskBody, user: User = Depends(current_user)):
    from novera.ai import Analyst

    return Analyst(settings.db_path).ask(body.question, body.run_id, session_id=body.session_id).to_dict()


@app.post("/analyst/commentary", response_model=s.AnalystAnswer)
def analyst_commentary(run_id: str | None = None, user: User = Depends(current_user)):
    from novera.ai import Analyst

    return Analyst(settings.db_path).commentary(run_id).to_dict()


@app.get("/analyst/history", response_model=list[s.AnalystAnswer])
def analyst_history(limit: int = 50):
    from novera.ai import Analyst

    return Analyst(settings.db_path).history(limit)


@app.get("/analyst/provider", response_model=s.ProviderInfo)
def analyst_provider():
    from novera.ai import make_provider

    p = make_provider(settings)
    return {"provider": p.name, "model": p.model, "chain": getattr(p, "chain", [])}


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


def _ops(user: User | None = None):
    from novera.api.agents_api import AgentOps

    return AgentOps(settings.db_path, user)


@app.post("/agents/investigate-breach", response_model=s.AgentNote)
def agent_investigate(body: InvestigateBody, user: User = Depends(current_user)):
    return _ops(user).investigate_breach(body.breach_id, body.attach)


@app.post("/agents/suggest-scenarios", response_model=s.AgentNote)
def agent_scenarios(body: ScenariosBody, user: User = Depends(current_user)):
    return _ops(user).suggest_scenarios(body.run_id, body.n)


@app.post("/agents/draft-validation", response_model=s.AgentNote)
def agent_validation(body: ValidationBody, user: User = Depends(current_user)):
    return _ops(user).draft_validation(body.run_id, body.records, body.run_tests)


@app.post("/agents/propose-csa", response_model=s.AgentNote)
def agent_propose_csa(body: DocumentBody, user: User = Depends(current_user)):
    return _ops(user).propose_csa(body.path)


@app.post("/agents/approve-csa", response_model=s.NoteDecision)
def agent_approve_csa(body: NoteDecisionBody, user: User = Depends(current_user)):
    return _ops(user).approve_csa(body.note_id)


@app.post("/agents/reject-csa", response_model=s.NoteDecision)
def agent_reject_csa(body: NoteDecisionBody, user: User = Depends(current_user)):
    return _ops(user).reject_csa(body.note_id, reason=body.reason)


@app.get("/agents/notes", response_model=list[s.AgentNote])
def agent_notes(kind: str | None = None, subject: str | None = None, limit: int = 50):
    return _ops().agent_notes(kind, subject, limit)


@app.get("/lab/catalogue", response_model=s.ProblemCatalogue)
def lab_catalogue():
    return _ops().problem_catalogue()


@app.post("/lab/run", response_model=s.LabRecord)
def lab_run(body: LabBody, user: User = Depends(current_user)):
    return _ops(user).run_lab(body.model_dump())


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
def risk_pack(
    run_id: str, pdf: bool = True, svc: RiskService = Depends(service), user: User = Depends(current_user)
):
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
