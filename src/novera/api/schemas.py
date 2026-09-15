"""Response schemas for the risk API.

Declared fields are the contract a client generates types from. Records that come out of
engine frames allow extra fields, so a new engine column reaches a screen before the schema
catches up; ``tests/test_api.py`` flags that drift so the schema never stays behind for long.
Grouped rows (``Row``) carry their grouping key as an extra field named after the dimension
(``desk_id``, ``asset_class``, ...) because the key depends on the ``by`` parameter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from novera.domain.counterparties import CSA, Counterparty, NettingSet
from novera.domain.organisation import Organisation
from novera.domain.trades import Trade
from novera.market_data.risk_factors import RiskFactor

__all__ = ["CSA", "Counterparty", "NettingSet", "Organisation", "RiskFactor", "Trade"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Row(ApiModel):
    """A grouped row. The dimension named by ``by`` arrives as an extra string field."""


# --- runs ---------------------------------------------------------------------------------
class Health(BaseModel):
    status: str
    platform: str
    version: str


class RegulatorySummary(ApiModel):
    frtb_sa: float | None = None
    frtb_sa_sbm: float | None = None
    frtb_sa_drc: float | None = None
    frtb_ima: float | None = None
    imes: float | None = None
    ses: float | None = None
    ima_multiplier: float | None = None
    nmrf: float | None = None
    saccr_ead: float | None = None
    saccr_rwa: float | None = None
    saccr_capital: float | None = None
    simm_im: float | None = None
    ba_cva_capital: float | None = None
    pla_red_desks: float | None = None


class CounterpartySummary(ApiModel):
    counterparties: int
    total_epe: float
    total_cva: float
    total_dva: float
    largest_pfe95: dict[str, Any] | None = None
    wrong_way_flags: int = 0
    paths: int


class RunFigures(ApiModel):
    """Headline figures stored with the run record (reporting currency)."""

    pv: float | None = None
    priced_trades: int = 0
    unpriced_trades: int = 0
    var: float | None = None
    es: float | None = None
    var_scaled: float | None = None
    var_scenario_date: str | None = None
    challenger_var: float | None = None
    monte_carlo_var: float | None = None
    monte_carlo_es: float | None = None
    backtest_zone: str | None = None
    backtest_exceptions: int | None = None
    backtest_days: int | None = None
    liquidity_adjusted_var: float | None = None
    liquidity_horizon_days: float | None = None
    concentration_flags: int | None = None
    liquidity_flags: int | None = None
    lookthrough_flags: int | None = None
    fund_holdings: int | None = None
    worst_stress_id: str | None = None
    worst_stress_name: str | None = None
    worst_stress: float | None = None
    limits_monitored: int | None = None
    breaches: int | None = None
    warnings: int | None = None
    breaches_raised: int | None = None
    breaches_auto_escalated: int | None = None
    breaches_back_within_limit: int | None = None
    dq_findings: int | None = None
    dq_verdict: str | None = None
    md_proxies: int | None = None
    pnl_total: float | None = None
    pnl_steps: dict[str, float] = Field(default_factory=dict)
    regulatory: RegulatorySummary | None = None
    counterparty: CounterpartySummary | None = None
    fund: dict[str, Any] | None = None
    alerts: dict[str, int] | None = None


class RunRecord(ApiModel):
    run_id: str
    run_type: str
    business_date: str
    portfolio_snapshot_id: str
    market_snapshot_id: str
    previous_market_snapshot_id: str | None = None
    reporting_currency: str
    model_versions: dict[str, str]
    config_hash: str | None = None
    status: str
    verdict: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    summary: RunFigures
    timings: dict[str, float] = Field(default_factory=dict)


class VarRow(Row):
    component_var: float
    component_es: float | None = None
    trades: int | None = None


class LimitRow(ApiModel):
    limit_id: str
    limit_type: str
    level: str
    entity_id: str
    filters: str | None = None
    amount: float
    base_amount: float
    increase_id: str | None = None
    current: float | None = None
    utilisation: float | None = None
    status: str
    warning_threshold: float
    owner: str
    trades_in_scope: int


class DQFinding(ApiModel):
    code: str
    severity: str
    subject: str
    message: str
    affected_trades: int
    owner: str
    affected_trade_ids: str | None = None


class RunSummary(RunRecord):
    """The morning screen in one call: run record plus the four things to look at first."""

    var_by_asset_class: list[VarRow]
    top_breaches: list[LimitRow]
    dq_findings: list[DQFinding]
    pnl_steps: dict[str, float] = Field(default_factory=dict)


# --- measures -----------------------------------------------------------------------------
class PositionRow(Row):
    pv: float | None = None
    trades: int
    unpriced: int = 0


class VarSummaryRow(ApiModel):
    method: str
    var: float
    es: float
    var_scaled: float | None = None
    es_scaled: float | None = None
    confidence: float
    es_confidence: float | None = None
    window_days: int
    scenarios: int
    var_scenario_date: str | None = None


class VarScenarioRow(ApiModel):
    scenario_date: str
    portfolio_pnl: float
    challenger_pnl: float | None = None


class SensitivityRow(Row):
    underlying: str | None = None
    bucket: str | None = None
    value: float


class StressRow(Row):
    """One scenario; when grouped, each group's P&L is an extra field named after the group."""

    scenario_id: str
    name: str
    kind: str
    description: str | None = None
    total: float


class PnLStep(ApiModel):
    step: str
    pnl: float


class PnLByRow(Row):
    TOTAL: float


class PnLResidualRow(ApiModel):
    trade_id: str
    predicted: float | None = None
    actual: float | None = None
    residual: float | None = None


class PnLChallenger(ApiModel):
    predicted: float
    actual: float
    residual: float
    worst_residuals: list[PnLResidualRow]


class PnLExplain(ApiModel):
    run_id: str
    steps: list[PnLStep]
    total: float | None = None
    by: list[PnLByRow] | None = None
    challenger: PnLChallenger | None = None


class TradeValuation(ApiModel):
    trade_id: str
    book_id: str
    desk_id: str
    business_id: str | None = None
    legal_entity_id: str | None = None
    trader_id: str | None = None
    counterparty_id: str | None = None
    asset_class: str
    product_type: str
    currency: str
    quantity: float
    direction: str
    status: str
    pv_local: float | None = None
    fx_to_reporting: float | None = None
    pv: float | None = None
    model: str | None = None
    model_version: str | None = None
    note: str | None = None
    error: str | None = None


class TradeSensitivityRow(ApiModel):
    trade_id: str
    measure: str
    factor_id: str | None = None
    bucket: str | None = None
    underlying: str | None = None
    bump: float | None = None
    value: float


class TradeStressRow(ApiModel):
    scenario_id: str
    trade_id: str
    pnl: float


class TradeDetail(ApiModel):
    run_id: str
    valuation: TradeValuation
    trade: Trade | None = Field(default=None, description="None when the trade left the snapshot")
    sensitivities: list[TradeSensitivityRow]
    stress: list[TradeStressRow]


class CounterpartyReference(ApiModel):
    """Who we face and under which agreements, independent of any run."""

    counterparties: list[Counterparty]
    netting_sets: list[NettingSet]
    csas: list[CSA]


class ProductField(ApiModel):
    name: str
    type: str
    required: bool
    default: Any = None
    description: str = ""


class ProductSpec(ApiModel):
    product_type: str
    name: str
    asset_class: str
    venue: str
    model: str = Field(description="Model name written on every valuation row")
    model_label: str
    model_version: str
    methodology: str = Field(description="Methodology record id, e.g. PR-012")
    methodology_title: str
    market_standard: str = Field(description="The model a desk or validation team would expect")
    simplifications: str = Field(description="Where the model used departs from the standard")
    appropriateness: str = Field(
        description="market_standard, acceptable_simplification or known_weakness (MV-001)"
    )
    appropriateness_label: str
    validation: str = Field(description="External benchmark or analytic identity, and the test")
    instrument_class: str | None = None
    fields: list[ProductField] = Field(default_factory=list)


class AssetClassProducts(ApiModel):
    asset_class: str
    name: str
    products: list[ProductSpec]


class ProductReference(ApiModel):
    """What the platform can price, grouped by asset class. Read from code, not from a run."""

    asset_classes: list[AssetClassProducts]


class ModelInventoryRow(ApiModel):
    product_type: str
    name: str
    asset_class: str
    asset_class_name: str
    model: str = Field(description="Model name written on every valuation row")
    model_label: str
    model_version: str
    methodology: str = Field(description="Pricing methodology record id, e.g. PR-002")
    market_standard: str
    simplifications: str
    appropriateness: str = Field(description="market_standard, acceptable_simplification or known_weakness")
    appropriateness_label: str
    validation: str


class ModelInventory(ApiModel):
    """One row per product: model used, market standard, simplifications, appropriateness
    rating and how the implementation is validated. Record MV-001; read from code."""

    record: str = Field(description="Methodology record id of the inventory, MV-001")
    version: str
    rows: list[ModelInventoryRow]
    summary: dict[str, int] = Field(description="Product count per appropriateness rating")


class MeasureSpec(ApiModel):
    methodology: str = Field(description="Methodology record id, e.g. MR-002; the measure's id")
    name: str
    definition: str
    unit: str
    screen: str = Field(description="Dashboard page where the measure is read")
    methodology_title: str
    version: str
    face: str = Field(description="bank, fund, or bank and fund")


class RiskArea(ApiModel):
    area: str
    name: str
    measures: list[MeasureSpec]


class MeasureReference(ApiModel):
    """Every risk measure by area, with its governing record. Read from code, not from a run."""

    areas: list[RiskArea]


class RiskFactorReference(ApiModel):
    """The stored risk-factor universe: curves, spots, spreads, surfaces and cubes."""

    factors: list[RiskFactor]


class MarketDataSourceRow(ApiModel):
    factor_id: str
    family: str = Field(description="Curve, surface, cube or single factor, e.g. IR:USD or VOL:SPX")
    factor_type: str
    asset_class: str
    currency: str
    underlying: str
    group: str = Field(description="Catalogue group the free and paid sources are written for")
    status: str = Field(description="REAL (fetched), AVAILABLE (adapter wired, not fetched) or SYNTHETIC")
    status_label: str
    source: str = Field(description="Adapter that fetched it, else the simulator record")
    adapter: str | None = Field(default=None, description="fred, yahoo or coinbase when an adapter maps it")
    fetched_at: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    row_count: int | None = None
    free_source: str
    paid_source: str
    notes: str = ""


class MarketDataSourceFamily(ApiModel):
    family: str
    group: str
    factor_type: str
    asset_class: str
    currency: str
    underlying: str
    factors: int
    real: int
    available: int
    synthetic: int
    status: str = Field(description="REAL, AVAILABLE, SYNTHETIC, or PARTIAL when nodes differ")
    status_label: str
    sources: list[str]
    adapters: list[str]
    last_date: str | None = None
    free_source: str
    paid_source: str
    notes: str = ""


class MarketDataSources(ApiModel):
    """Where every risk factor's history comes from today, and where real data could come
    from: one row per factor, one per family, and the totals. Record MD-001."""

    record: str
    rows: list[MarketDataSourceRow]
    families: list[MarketDataSourceFamily]
    summary: dict[str, int] = Field(description="Factor and family counts per status")


class RerunStage(ApiModel):
    name: str
    title: str
    description: str
    tables: list[str] = Field(description="Result tables the stage replaces on the new run")
    dependents: list[str] = Field(description="Stages copied from the parent that are not recomputed")
    summary_keys: list[str]
    faces: list[str] = Field(description="bank, fund or both")


class RerunRecord(RunRecord):
    """A RERUN run: the parent's results copied under a new run id with one stage recomputed."""

    rerun: dict[str, Any] = Field(
        description="parent_run_id, stage, actor, reason, stale_stages, changed (before and after per "
        "summary key), seconds"
    )


class RerunOptions(ApiModel):
    """What an administrator can re-run and what has been re-run. Record OPS-002."""

    record: str
    stages: list[RerunStage]
    reruns: list[RerunRecord]


class StressShock(ApiModel):
    target: str = Field(description="Factor id or family prefix the shock applies to")
    family: str
    size: float
    unit: str = Field(description="bp, vol points or %")
    tenors: str | None = Field(default=None, description="Curve nodes the rule is limited to")
    text: str
    kind: str = Field(description="RULE (hypothetical) or REALISED (observed move in the history)")


class StressScenarioSpec(ApiModel):
    scenario_id: str
    name: str
    category: str
    kind: str = Field(description="HYPOTHETICAL or HISTORICAL, as the engine runs it")
    description: str
    in_daily_run: bool
    status: str = Field(description="IN_RUN, NOT_COVERED, SYNTHETIC_WINDOW, PARTLY_REAL or REAL")
    window_start: str | None = None
    window_end: str | None = None
    shocks: list[StressShock]
    shock_count: int
    real_share: float | None = None
    note: str | None = None


class StressCategory(ApiModel):
    category: str
    title: str
    description: str
    in_daily_run: bool
    count: int
    scenarios: list[StressScenarioSpec]


class StressLibrary(ApiModel):
    """The stress library by category with the shocks each scenario applies, and for
    historical windows the realised moves of headline factors. Record MR-005."""

    record: str
    history: dict[str, Any]
    categories: list[StressCategory]
    summary: dict[str, int]


class AuditEvent(ApiModel):
    event_id: str
    at: str
    actor: str
    event_type: str
    subject: str | None = None
    payload: str | None = None


# --- trade extract ------------------------------------------------------------------------
class TradeExtractRow(Row):
    """One trade of the run with its valuation; the instrument's terms (issuer, coupon,
    strike, expiry, pair, ...) arrive as extra columns named after the instrument fields."""

    trade_id: str
    business_id: str | None = None
    desk_id: str | None = None
    book_id: str | None = None
    legal_entity_id: str | None = None
    trader_id: str | None = None
    counterparty_id: str | None = None
    netting_set_id: str | None = None
    clearing: str | None = None
    asset_class: str
    product_type: str
    instrument_id: str | None = None
    description: str | None = None
    currency: str
    direction: str
    swap_side: str | None = None
    quantity: float
    trade_price: float | None = None
    trade_date: str | None = None
    settlement_date: str | None = None
    maturity_date: str | None = Field(
        default=None, description="Maturity, expiry or end date, whichever applies"
    )
    status: str
    source_system: str | None = None
    version: int | None = None
    pv_local: float | None = None
    fx_to_reporting: float | None = None
    pv: float | None = None
    model: str | None = None
    model_version: str | None = None
    note: str | None = None
    error: str | None = None


class TradeExtract(ApiModel):
    run_id: str
    business_date: str
    reporting_currency: str
    count: int
    pv_total: float
    columns: list[str] = Field(description="Column order of the CSV")
    rows: list[TradeExtractRow]


class DateRange(ApiModel):
    min: str | None = None
    max: str | None = None


class TradeExtractOptions(ApiModel):
    """Distinct values per filter dimension in the run, to populate a filter form."""

    run_id: str
    business_date: str
    dims: dict[str, list[str]]
    dates: dict[str, DateRange]


# --- limit management ---------------------------------------------------------------------
class LimitHierarchyRow(ApiModel):
    """A limit definition placed in the firm hierarchy, with the selected run's figures."""

    limit_id: str
    limit_type: str
    level: str
    level_rank: int
    entity_id: str
    node_name: str
    path: str = Field(description="Hierarchy path, e.g. Global Macro Bank › Macro › USD Rates")
    filters: str = ""
    unit: str
    amount: float
    warning_threshold: float
    owner: str
    approver: str = ""
    approval_status: str
    effective_from: str
    effective_to: str | None = None
    rationale: str = ""
    effective_amount: float = Field(description="Amount in force on the run, after temporary increases")
    increase_id: str | None = None
    current: float | None = None
    utilisation: float | None = None
    status: str = Field(description="OK, WARNING, BREACH, NO_DATA or NO_RUN")
    trades_in_scope: int | None = None


class LimitHierarchy(ApiModel):
    run_id: str | None = None
    business_date: str | None = None
    rows: list[LimitHierarchyRow]


# --- breach workflow ----------------------------------------------------------------------
class BreachRecord(ApiModel):
    breach_id: str
    limit_id: str
    limit_type: str
    level: str
    entity_id: str
    owner: str
    status: str
    first_run_id: str
    latest_run_id: str
    first_date: str
    latest_date: str
    consecutive_days: int
    first_utilisation: float
    latest_utilisation: float
    peak_utilisation: float
    escalated_to: str | None = None
    acknowledged_by: str | None = None
    closed_by: str | None = None
    close_reason: str | None = None
    closed_at: str | None = None
    within_limit_on_latest_run: bool = False


class BreachActionRecord(ApiModel):
    action_id: str
    breach_id: str
    at: str
    actor: str
    action: str
    comment: str | None = None
    run_id: str | None = None
    escalated_to: str | None = None
    close_reason: str | None = None


class IncreaseRecord(ApiModel):
    increase_id: str
    limit_id: str
    base_amount: float
    new_amount: float
    effective_from: str
    expires_on: str
    requested_by: str
    requested_at: str
    rationale: str
    status: str
    decided_by: str | None = None
    decided_at: str | None = None
    decision_comment: str | None = None
    breach_id: str | None = None
    allowed_approvers: list[str] = Field(default_factory=list)
    increase_pct: float | None = None


class BreachDetail(BreachRecord):
    actions: list[BreachActionRecord]
    increases: list[IncreaseRecord]


# --- run comparison -----------------------------------------------------------------------
class CompareValue(ApiModel):
    a: float | None = None
    b: float | None = None
    change: float | None = None


class CompareVarRow(Row):
    a: float
    b: float
    change: float


class LimitChangeRow(ApiModel):
    limit_id: str
    status_a: str | None = None
    utilisation_a: float | None = None
    amount_a: float | None = None
    status_b: str | None = None
    utilisation_b: float | None = None
    amount_b: float | None = None
    owner: str | None = None


class TradeChangeRow(ApiModel):
    trade_id: str
    desk_id: str | None = None
    product_type: str | None = None
    pv_a: float | None = None
    pv_b: float | None = None
    pv_change: float
    presence: str


class RunComparison(ApiModel):
    a: RunRecord
    b: RunRecord
    headline: dict[str, CompareValue]
    var_by: list[CompareVarRow]
    limit_changes: list[LimitChangeRow]
    top_trade_changes: list[TradeChangeRow]
    trades_only_in_a: int
    trades_only_in_b: int
    breaches: list[BreachRecord]


# --- Risk Copilot and agents --------------------------------------------------------------
class ToolCall(ApiModel):
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False
    seconds: float = 0.0
    output: str | None = None


class CopilotAnswer(ApiModel):
    answer_id: str
    question: str
    answer: str
    run_id: str | None = None
    provider: str
    model: str | None = None
    run_ids_cited: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    turns: int = 0
    seconds: float = 0.0
    session_id: str | None = None
    at: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    provider: str
    model: str | None = None


class AgentNote(ApiModel):
    note_id: str
    kind: str
    subject: str | None = None
    run_id: str | None = None
    provider: str
    model: str | None = None
    status: str
    text: str
    evidence: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    seconds: float = 0.0
    at: str
    path: str | None = Field(default=None, description="Report file written by the validation agent")


class NoteDecision(ApiModel):
    note_id: str
    status: str
    netting_set_id: str | None = None
    csa_id: str | None = None


class ProblemEntry(ApiModel):
    name: str
    title: str


class ProblemCatalogue(ApiModel):
    bank: list[ProblemEntry]
    hedge_fund: list[ProblemEntry]
    market: list[ProblemEntry]


class LabInjection(ApiModel):
    name: str
    description: str | None = None
    trade_ids: list[str] = Field(default_factory=list)
    expected_detection: str | None = None


class LabDetection(ApiModel):
    problem: str
    title: str | None = None
    description: str | None = None
    expected: str | None = None
    detected: bool
    evidence: list[Any] = Field(default_factory=list)
    context: list[Any] = Field(default_factory=list)
    needs: str | None = None


class LabSummary(ApiModel):
    business_date: str
    verdict: str | None = None
    var: float | None = None
    breaches: int = 0
    warnings: int = 0
    dq_findings: int = 0
    trades: int = 0


class LabRecord(ApiModel):
    name: str
    db_path: str
    created_at: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    injections: list[LabInjection] = Field(default_factory=list)
    planted_market: list[str] = Field(default_factory=list)
    run_id: str | None = None
    detections: list[LabDetection] = Field(default_factory=list)
    summary: LabSummary | None = None


# --- operations ---------------------------------------------------------------------------
class AlertRecord(ApiModel):
    alert_id: str
    at: str
    business_date: str | None = None
    severity: str
    kind: str
    subject: str | None = None
    title: str
    body: str | None = None
    recipients: list[str] = Field(default_factory=list)
    run_id: str | None = None
    status: str
    deliveries: dict[str, Any] = Field(default_factory=dict)


class JobRecord(ApiModel):
    job_id: str
    started_at: str
    action: str
    business_date: str | None = None
    run_id: str | None = None
    status: str
    attempts: int = 0
    error: str | None = None
    finished_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class ReconciliationDesk(ApiModel):
    desk_id: str
    novera_var: float | None = None
    official_var: float | None = None
    trades_both: int = 0
    pv_mismatches: int = 0
    gap: float | None = None
    recon_id: str | None = None


class ReconciliationDifference(ApiModel):
    trade_id: str
    desk_id: str | None = None
    book_id: str | None = None
    product_type: str | None = None
    asset_class: str | None = None
    pv: float | None = None
    var_contribution: float | None = None
    official_pv: float | None = None
    official_var_contribution: float | None = None
    presence: str | None = None
    pv_diff: float | None = None
    var_diff: float | None = None
    pv_matches: bool | None = None
    cause: str | None = None
    recon_id: str | None = None


class Reconciliation(ApiModel):
    recon_id: str
    vendor: str
    business_date: str
    novera_var: float
    official_var: float
    gap: float
    gap_pct: float | None = None
    attribution: dict[str, float] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    trades_compared: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)
    run_id: str
    by_desk: list[ReconciliationDesk]
    cause_counts: dict[str, int] = Field(default_factory=dict)
    largest_differences: list[ReconciliationDifference]


class ProvenanceRow(ApiModel):
    factor_id: str
    source: str
    fetched_at: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    row_count: int | None = None


class ConcentrationDimension(ApiModel):
    dimension: str
    basis: str
    groups: int
    hhi: float
    effective_number: float
    top1_share: float
    top5_share: float | None = None
    top10_share: float | None = None
    largest: str | None = None


class TopPosition(ApiModel):
    trade_id: str
    desk_id: str | None = None
    book_id: str | None = None
    product_type: str | None = None
    counterparty_id: str | None = None
    pv: float | None = None
    var_contribution: float
    share_of_var: float


class TenorConcentration(ApiModel):
    currency: str
    bucket: str
    dv01: float
    share_of_abs_dv01: float


class ConcentrationReport(ApiModel):
    run_id: str
    by_dimension: list[ConcentrationDimension]
    top_positions: list[TopPosition]
    tenor: list[TenorConcentration]
    flags: list[str] = Field(default_factory=list)


class LiquidityBucket(ApiModel):
    horizon_bucket: str
    trades: int
    abs_pv: float
    share_of_abs_pv: float


class LiquidityDesk(ApiModel):
    desk_id: str | None = None
    weighted_days: float
    max_days: float
    bidask_cost: float
    trades: float


class SlowestPosition(ApiModel):
    trade_id: str
    desk_id: str | None = None
    book_id: str | None = None
    product_type: str | None = None
    position: float
    adv: float | None = None
    days_to_liquidate: float
    bidask_cost: float | None = None
    horizon_bucket: str
    pv: float | None = None


class LiquidityReport(ApiModel):
    run_id: str
    var: float
    liquidity_adjusted_var: float
    horizon_days: float
    by_bucket: list[LiquidityBucket]
    by_desk: list[LiquidityDesk]
    slowest: list[SlowestPosition]
    flags: list[str] = Field(default_factory=list)


class LookthroughFund(ApiModel):
    fund: str
    product_type: str
    trades: int
    exposure: float
    constituents: int


class LookthroughHolding(ApiModel):
    trade_id: str
    book_id: str | None = None
    fund: str
    product_type: str | None = None
    constituent: str
    kind: str
    leg_currency: str | None = None
    units: float
    exposure: float
    share_of_fund: float


class LookthroughConstituent(ApiModel):
    constituent: str
    kind: str
    direct: float
    via_funds: float
    total: float
    via_funds_share: float


class LookthroughReport(ApiModel):
    run_id: str
    fund_trades: int
    by_fund: list[LookthroughFund] = Field(default_factory=list)
    holdings: list[LookthroughHolding] = Field(default_factory=list)
    constituents: list[LookthroughConstituent] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ProxyAction(ApiModel):
    kind: str
    factor_id: str | None = None
    source: str | None = Field(default=None, description="Proxy family or factor the value came from")
    original: float | None = None
    value: float | None = None
    reason: str | None = None


class MarketDataProxies(ApiModel):
    run_id: str
    raw_market_snapshot_id: str | None = None
    applied: int
    kept_stale: int
    actions: list[ProxyAction] = Field(default_factory=list)


class BacktestSummaryRow(ApiModel):
    kind: str
    confidence: float
    days: int
    exceptions: int
    expected_exceptions: float
    kupiec_lr: float | None = None
    kupiec_pvalue: float | None = None
    christoffersen_lr: float | None = None
    christoffersen_pvalue: float | None = None
    conditional_lr: float | None = None
    conditional_pvalue: float | None = None
    zone: str
    lookback_days: int | None = None


class BacktestPoint(ApiModel):
    date: str
    pnl: float
    var: float
    exception: bool
    kind: str


class BacktestReport(ApiModel):
    run_id: str
    summary: list[BacktestSummaryRow]
    series: list[BacktestPoint]
    live_series: list[BacktestPoint] = Field(default_factory=list)


class RiskPackFiles(BaseModel):
    html: str
    xlsx: str
    pdf: str | None = None


# --- counterparty risk --------------------------------------------------------------------
class CounterpartyExposureRow(ApiModel):
    counterparty_id: str
    epe: float
    eepe: float | None = None
    peak_pfe95: float
    peak_pfe99: float | None = None
    peak_pfe95_gross: float | None = None
    peak_pfe95_step: str | None = None
    ee_1y: float | None = None
    collateralised: bool
    cva: float | None = None
    dva: float | None = None
    bcva: float | None = None
    cva_gross: float | None = None
    wwr_correlation: float | None = None
    wrong_way: bool = False
    wwr_proxy: str | None = None
    current_exposure: float | None = None
    netting_sets: int
    trades: int
    name: str | None = None
    counterparty_type: str | None = None
    rating: str | None = None
    country: str | None = None
    on_watchlist: bool = False


class StressedExposureRow(ApiModel):
    scenario_id: str
    counterparty_id: str
    current_exposure: float
    stressed_exposure: float
    increase: float


class CounterpartyExposures(ApiModel):
    run_id: str
    summary: list[CounterpartyExposureRow]
    notes: dict[str, str] = Field(default_factory=dict)
    stressed: list[StressedExposureRow] = Field(default_factory=list)


class ExposureProfilePoint(ApiModel):
    step: str
    years: float
    ee: float
    ee_gross: float | None = None
    pfe95: float
    pfe99: float | None = None
    pfe95_gross: float | None = None
    ene: float | None = None
    mean_collateral: float | None = None


class NettingSetRecord(ApiModel):
    netting_set_id: str
    counterparty_id: str
    legal_entity_id: str | None = None
    agreement_type: str | None = None
    csa_id: str | None = None
    csa: dict[str, Any] | None = None


class NettingSummaryRow(ApiModel):
    netting_set_id: str
    epe: float
    eepe: float | None = None
    peak_pfe95: float
    peak_pfe99: float | None = None
    peak_pfe95_gross: float | None = None
    peak_pfe95_step: str | None = None
    ee_1y: float | None = None
    collateralised: bool


class CVARow(ApiModel):
    netting_set_id: str
    counterparty_id: str
    pd_1y: float
    hazard: float
    cva: float
    dva: float
    bcva: float
    cva_gross: float | None = None


class WrongWayRow(ApiModel):
    netting_set_id: str
    counterparty_id: str
    proxy: str | None = None
    correlation: float | None = None
    at_step: str | None = None
    wrong_way: bool


class CounterpartyDetail(ApiModel):
    run_id: str
    counterparty: Counterparty
    profile: list[ExposureProfilePoint]
    netting_sets: list[NettingSetRecord]
    netting_summary: list[NettingSummaryRow]
    cva: list[CVARow]
    wwr: list[WrongWayRow]
    stressed: list[StressedExposureRow]
    trades: list[TradeValuation]


class CSAWhatIf(ApiModel):
    run_id: str | None = None
    netting_set_id: str | None = None
    base_csa: dict[str, Any] | None = None
    what_if_csa: dict[str, Any] | None = None
    before: list[ExposureProfilePoint]
    after: list[ExposureProfilePoint]


# --- regulatory capital -------------------------------------------------------------------
class CapitalComponent(ApiModel):
    component: str
    capital: float
    basis: str | None = None


class CapitalByDesk(ApiModel):
    desk_id: str
    frtb_sa: float | None = None
    frtb_ima: float | None = None
    saccr: float | None = None
    ba_cva: float | None = None
    total_sa: float | None = None
    business_id: str | None = None


class FRTBClassRow(ApiModel):
    risk_class: str
    delta: float
    vega: float
    curvature: float
    scenario: str | None = None


class FRTBDetailRow(ApiModel):
    risk_class: str
    bucket: str
    factor: str
    weighted_sensitivity: float


class IMARow(ApiModel):
    imes: float
    ses: float
    nmrf: int
    multiplier: float
    backtest_exceptions: int
    capital: float


class PLARow(ApiModel):
    desk_id: str
    spearman: float | None = None
    ks: float | None = None
    zone: str
    trades: int


class SACCRCounterpartyRow(ApiModel):
    counterparty_id: str
    ead: float
    rc: float
    pfe: float
    netting_sets: int
    risk_weight: float
    rwa: float
    capital: float


class SACCRNettingRow(Row):
    """Add-ons by hedging set arrive as extra ``addon_<class>`` fields."""

    netting_set_id: str
    counterparty_id: str
    margined: bool
    value: float
    collateral: float
    replacement_cost: float
    addon: float
    multiplier: float
    pfe: float
    ead: float


class SIMMRow(Row):
    """Margin by risk class arrives as extra fields named after the class."""

    netting_set_id: str
    counterparty_id: str
    im: float


class BACVARow(ApiModel):
    counterparty_id: str
    sector: str | None = None
    investment_grade: bool
    risk_weight: float
    maturity: float
    ead: float
    scva: float


class CashLadderRow(ApiModel):
    currency: str
    bucket: str
    inflow: float
    outflow: float
    net: float
    flows: int
    cumulative_net: float


class CapitalReport(ApiModel):
    run_id: str
    available: bool
    summary: RegulatorySummary | None = None
    components: list[CapitalComponent] = Field(default_factory=list)
    by_desk: list[CapitalByDesk] = Field(default_factory=list)
    frtb_sa_classes: list[FRTBClassRow] = Field(default_factory=list)
    frtb_sa_detail: list[FRTBDetailRow] = Field(default_factory=list)
    ima: list[IMARow] = Field(default_factory=list)
    pla: list[PLARow] = Field(default_factory=list)
    saccr_counterparty: list[SACCRCounterpartyRow] = Field(default_factory=list)
    saccr_netting: list[SACCRNettingRow] = Field(default_factory=list)
    simm: list[SIMMRow] = Field(default_factory=list)
    ba_cva: list[BACVARow] = Field(default_factory=list)
    cash_ladder: list[CashLadderRow] = Field(default_factory=list)


# --- fund face ----------------------------------------------------------------------------
class FundFigures(ApiModel):
    nav: float
    gross_leverage: float
    net_leverage: float
    long: float
    short: float
    total_margin: float
    margin_to_nav: float
    largest_pb_share: float
    crowding_score: float
    crowded_share_of_gross: float
    worst_redemption_scenario: str | None = None
    worst_redemption_coverage: float | None = None
    redemption_shortfall: float | None = None


class FundProfile(ApiModel):
    firm_id: str
    name: str
    currency: str
    nav: float
    inception: str | None = None
    management_fee: float | None = None
    performance_fee: float | None = None
    target_gross_leverage: float | None = None
    max_gross_leverage: float | None = None
    investors: list[dict[str, Any]] = Field(default_factory=list)
    prime_brokers: list[str] = Field(default_factory=list)


class ExposureRow(Row):
    long: float
    short: float
    gross: float
    net: float
    long_pct_nav: float
    short_pct_nav: float
    gross_pct_nav: float
    net_pct_nav: float


class MarginByBroker(ApiModel):
    prime_broker: str
    gross_exposure: float
    margin: float
    trades: int
    share: float


class MarginDetail(ApiModel):
    prime_broker: str
    asset_class: str
    gross_exposure: float
    margin_gross: float
    netting_benefit: float
    margin: float
    trades: int


class FactorBeta(ApiModel):
    strategy: str
    factor: str
    label: str | None = None
    beta_per_sigma: float
    beta_pct_nav: float
    t_stat: float | None = None
    r2: float | None = None
    residual_vol: float | None = None


class RedemptionScenario(ApiModel):
    scenario: str
    dealing_date: str
    business_days: int
    redemptions_due: float
    cumulative_due: float
    liquidatable_by_then: float
    gated: float
    coverage: float
    shortfall: float


class StrategyAttribution(ApiModel):
    strategy: str
    pv: float
    gross_pct_nav: float
    net_pct_nav: float
    component_var: float
    share_of_var: float
    pnl_today: float
    hypothetical_ann_return_pct_nav: float | None = None
    hypothetical_ann_vol_pct_nav: float | None = None
    sharpe_like: float | None = None
    max_drawdown: float | None = None
    trades: int


class CrowdingRow(ApiModel):
    underlying: str
    exposure: float
    score: float
    trades: int
    abs_exposure: float
    pct_nav: float
    crowded: bool
    days_to_liquidate: float | None = None
    crowded_exit_days: float | None = None


class FundReport(ApiModel):
    run_id: str
    available: bool
    summary: FundFigures | None = None
    fund: FundProfile | None = None
    var: float | None = None
    es: float | None = None
    worst_stress: float | None = None
    worst_stress_name: str | None = None
    exposure_strategy: list[ExposureRow] = Field(default_factory=list)
    exposure_asset_class: list[ExposureRow] = Field(default_factory=list)
    margin_pb: list[MarginByBroker] = Field(default_factory=list)
    margin_detail: list[MarginDetail] = Field(default_factory=list)
    factors: list[FactorBeta] = Field(default_factory=list)
    redemptions: list[RedemptionScenario] = Field(default_factory=list)
    attribution: list[StrategyAttribution] = Field(default_factory=list)
    crowding: list[CrowdingRow] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
