"""Breach lifecycle and temporary limit increases (approval matrix).

Breach:   OPEN -> ACKNOWLEDGED -> ESCALATED -> CLOSED. Escalation can happen from OPEN or
          ACKNOWLEDGED, by a person or automatically. CLOSED needs a reason and is only
          allowed when the limit is back within threshold, a temporary increase covers it,
          the limit was retired, or the breach is declared a false positive with a comment.
Increase: REQUESTED -> APPROVED | REJECTED, then EXPIRED when past its end date, or
          CANCELLED by the requester. Approved increases change the effective limit amount
          used by monitoring while they are in force.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novera.domain.enums import HierarchyLevel


class BreachStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class BreachActionType(StrEnum):
    RAISED = "RAISED"
    UPDATED = "UPDATED"  # seen again on a later run
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ESCALATED = "ESCALATED"
    AUTO_ESCALATED = "AUTO_ESCALATED"
    CLOSED = "CLOSED"
    COMMENT = "COMMENT"


class CloseReason(StrEnum):
    RISK_REDUCED = "RISK_REDUCED"
    TEMPORARY_INCREASE_APPROVED = "TEMPORARY_INCREASE_APPROVED"
    LIMIT_RETIRED = "LIMIT_RETIRED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class BreachAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    breach_id: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    action: BreachActionType
    comment: str = ""
    run_id: str | None = None
    escalated_to: str | None = None
    close_reason: CloseReason | None = None


class Breach(BaseModel):
    """Mutable workflow state; one open breach per limit at a time."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("first_utilisation", "latest_utilisation", "peak_utilisation", mode="before")
    @classmethod
    def _none_to_zero(cls, v):
        return 0.0 if v is None else v

    breach_id: str
    limit_id: str
    limit_type: str
    level: str
    entity_id: str
    owner: str
    status: BreachStatus = BreachStatus.OPEN
    first_run_id: str
    latest_run_id: str
    first_date: date
    latest_date: date
    consecutive_days: int = 1
    first_utilisation: float = 0.0
    latest_utilisation: float = 0.0
    peak_utilisation: float = 0.0
    escalated_to: str | None = None
    acknowledged_by: str | None = None
    closed_by: str | None = None
    close_reason: CloseReason | None = None
    closed_at: datetime | None = None
    within_limit_on_latest_run: bool = False

    @property
    def is_open(self) -> bool:
        return self.status is not BreachStatus.CLOSED


class IncreaseStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# Who may approve a temporary increase, by the level of the limit. The CRO can approve
# anything; increases above ``CRO_THRESHOLD`` of the base amount always need the CRO.
APPROVERS_BY_LEVEL: dict[HierarchyLevel, tuple[str, ...]] = {
    HierarchyLevel.FIRM: ("CRO",),
    HierarchyLevel.BUSINESS: ("CRO",),
    HierarchyLevel.LEGAL_ENTITY: ("CRO",),
    HierarchyLevel.DESK: ("Head of Market Risk", "CRO"),
    HierarchyLevel.BOOK: ("Head of Market Risk", "CRO"),
    HierarchyLevel.TRADER: ("Head of Market Risk", "CRO"),
    HierarchyLevel.ASSET_CLASS: ("Head of Market Risk", "CRO"),
    HierarchyLevel.COUNTERPARTY: ("Head of Counterparty Risk", "CRO"),
    HierarchyLevel.CURRENCY: ("Head of Market Risk", "CRO"),
    HierarchyLevel.PRODUCT_TYPE: ("Head of Market Risk", "CRO"),
    HierarchyLevel.TRADE: ("Head of Market Risk", "CRO"),
}
CRO_THRESHOLD = 0.25  # increases above +25% of the base amount need the CRO
MAX_INCREASE_DAYS = 45  # calendar days


class LimitIncrease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    increase_id: str
    limit_id: str
    base_amount: float = Field(gt=0)
    new_amount: float = Field(gt=0)
    effective_from: date
    expires_on: date
    requested_by: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rationale: str = Field(min_length=1)
    status: IncreaseStatus = IncreaseStatus.REQUESTED
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str = ""
    breach_id: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> LimitIncrease:
        if self.new_amount <= self.base_amount:
            raise ValueError("new_amount must exceed the base amount")
        if self.expires_on < self.effective_from:
            raise ValueError("expires_on cannot precede effective_from")
        if (self.expires_on - self.effective_from).days > MAX_INCREASE_DAYS:
            raise ValueError(f"temporary increases are limited to {MAX_INCREASE_DAYS} days")
        return self

    @property
    def increase_pct(self) -> float:
        return self.new_amount / self.base_amount - 1.0

    def needs_cro(self) -> bool:
        return self.increase_pct > CRO_THRESHOLD

    def in_force(self, on: date) -> bool:
        return self.status is IncreaseStatus.APPROVED and self.effective_from <= on <= self.expires_on
