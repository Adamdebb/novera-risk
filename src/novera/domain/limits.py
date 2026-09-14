"""Limit definitions. Utilisation and breach lifecycle live in ``novera.limits``."""
from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novera.domain.enums import HierarchyLevel


class LimitType(StrEnum):
    VAR = "VAR"
    EXPECTED_SHORTFALL = "EXPECTED_SHORTFALL"
    STRESS_LOSS = "STRESS_LOSS"
    DV01 = "DV01"
    CS01 = "CS01"
    FX_DELTA = "FX_DELTA"
    EQUITY_DELTA = "EQUITY_DELTA"
    COMMODITY_DELTA = "COMMODITY_DELTA"
    VEGA = "VEGA"
    GAMMA = "GAMMA"
    NOTIONAL = "NOTIONAL"
    CONCENTRATION = "CONCENTRATION"
    COUNTERPARTY_EXPOSURE = "COUNTERPARTY_EXPOSURE"
    LIQUIDITY = "LIQUIDITY"


class LimitStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class LimitScope(BaseModel):
    """Where a limit applies: a hierarchy node plus optional dimension filters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: HierarchyLevel
    entity_id: str = Field(description="ID of the node at ``level``, e.g. a desk_id")
    currency: str | None = None
    asset_class: str | None = None
    tenor_bucket: str | None = Field(default=None, description="e.g. 10Y for a DV01 ladder limit")
    risk_factor: str | None = Field(default=None, description="e.g. BRENT for concentration")


class Limit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit_id: str
    limit_type: LimitType
    scope: LimitScope
    amount: float = Field(gt=0, description="Absolute limit in reporting currency or units")
    warning_threshold: float = Field(default=0.8, gt=0, le=1, description="Fraction of amount")
    owner: str = Field(description="Accountable person or role, e.g. Head of Rates")
    approver: str = ""
    status: LimitStatus = LimitStatus.APPROVED
    effective_from: date
    effective_to: date | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _dates(self) -> Limit:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")
        return self

    def is_effective(self, on: date) -> bool:
        return (
            self.status is LimitStatus.APPROVED
            and self.effective_from <= on
            and (self.effective_to is None or on <= self.effective_to)
        )
