"""One error contract for every consumer of the API.

Over HTTP an error is an RFC 9457 Problem Details document (``application/problem+json``)
with a machine-readable ``code``. In process, the local client raises the same ``ApiError``
classes, so a screen never needs to import an engine exception to tell a workflow rule
from a missing run.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiError(Exception):
    """Base of every error the API reports. ``status`` and ``code`` are the contract."""

    status: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "Internal error"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        if status:
            self.status = status
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        return self.detail


class NotFoundError(ApiError):
    status = 404
    code = "NOT_FOUND"
    title = "Not found"


class RunNotFoundError(NotFoundError):
    """The run id does not exist, or ``latest`` was asked for on an empty database."""

    code = "RUN_NOT_FOUND"

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} not found", context={"run_id": run_id})
        self.run_id = run_id


class ConflictError(ApiError):
    """A workflow transition or approval that the rules do not allow."""

    status = 409
    code = "WORKFLOW_CONFLICT"
    title = "Workflow rule violated"


class InvalidRequestError(ApiError):
    status = 422
    code = "VALIDATION_FAILED"
    title = "Validation failed"


class Problem(BaseModel):
    """RFC 9457 Problem Details, plus ``code`` so clients branch on a stable token."""

    type: str = Field(default="about:blank", description="URI reference identifying the problem type")
    title: str
    status: int
    detail: str
    instance: str | None = Field(default=None, description="Request path that raised the problem")
    code: str = Field(description="Stable machine-readable code, e.g. RUN_NOT_FOUND")
    context: dict[str, Any] = Field(default_factory=dict, description="Identifiers relevant to the problem")


PROBLEM_MEDIA_TYPE = "application/problem+json"

_BY_CODE: dict[str, type[ApiError]] = {
    RunNotFoundError.code: RunNotFoundError,
    NotFoundError.code: NotFoundError,
    ConflictError.code: ConflictError,
    InvalidRequestError.code: InvalidRequestError,
}
_BY_STATUS: dict[int, type[ApiError]] = {404: NotFoundError, 409: ConflictError, 422: InvalidRequestError}


def translate(exc: BaseException) -> ApiError | None:
    """Map an engine or service exception onto the API contract; None means 'not ours'."""
    if isinstance(exc, ApiError):
        return exc
    from novera.limits import WorkflowError

    if isinstance(exc, WorkflowError):
        return ConflictError(str(exc))
    if isinstance(exc, KeyError):
        key = exc.args[0] if exc.args else ""
        return NotFoundError(str(key))
    if isinstance(exc, ValueError):
        return InvalidRequestError(str(exc))
    return None


def problem_from(err: ApiError, instance: str | None = None) -> Problem:
    return Problem(
        title=err.title,
        status=err.status,
        detail=err.detail,
        instance=instance,
        code=err.code,
        context=err.context,
    )


def error_from_problem(payload: dict[str, Any], status: int, fallback: str = "") -> ApiError:
    """Rebuild the typed error on the client side from a problem document."""
    code = str(payload.get("code") or "")
    detail = str(payload.get("detail") or fallback or f"HTTP {status}")
    context = payload.get("context") or {}
    cls = _BY_CODE.get(code) or _BY_STATUS.get(status) or ApiError
    if cls is RunNotFoundError:
        return RunNotFoundError(str(context.get("run_id", "?")))
    err = cls(detail, context=context)
    if code:
        err.code = code
    err.status = status
    return err
