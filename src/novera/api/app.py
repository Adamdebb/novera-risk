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
