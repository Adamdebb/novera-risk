"""Stress paths with management actions (MR-023, decision round 51): the owner's action rules, kept
in the database and edited from the Admin page, and the EOD stage that walks every path frozen and
managed. A run records the rules it applied, so a re-run of the stage reproduces it whatever the
rules say later."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from novera.domain.instruments import Repo
from novera.domain.limits import Limit
from novera.limits.workflow import WorkflowError
from novera.market_data.history import MarketHistory
from novera.risk.funding_chain import (
    CONTRACTUAL_KINDS,
    FUNDING_SCENARIOS,
    LEVEL1_CURRENCIES,
    LINEAR_EXPOSURE,
    RESOURCES,
    FundingInputs,
    FundingRules,
    business_dates,
    regime_of,
)
from novera.risk.funding_chain import RECORD as FUNDING_RECORD
from novera.risk.pca_space import HORIZON as PCA_HORIZON
from novera.risk.pca_space import PCASpace, QuadBook, build_space, quad_book
from novera.risk.revaluation import Portfolio
from novera.risk.reverse_stress import targets_from_limits
from novera.risk.stress_paths import (
    DEFAULT_RULES,
    DEFAULT_SCOPE,
    RECORD,
    ActionRule,
    StressPathsResult,
    run_stress_paths,
    validate_rules,
)
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

STRESS_PATH_TABLES = (
    "stress_paths",
    "stress_path_days",
    "stress_path_desks",
    "stress_path_actions",
    "stress_path_parameters",
    "stress_liquidity",
    "stress_liquidity_days",
    "stress_liquidity_instant",
    "stress_liquidity_parameters",
)
SUBJECT = "stress_action_rules"
FUNDING_SUBJECT = "stress_funding_rules"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _desks(repo: DuckDBRepository) -> list[str]:
    out: set[str] = set()
    for fid in repo.firm_ids():
        out |= {d.desk_id for d in repo.load_organisation(fid).desks}
    return sorted(out)


def stored_rules(repo: DuckDBRepository) -> list[ActionRule]:
    return [ActionRule.from_dict(r["rule"]) for r in repo.load_stress_action_rules()]


def rules_for_run(repo: DuckDBRepository) -> list[ActionRule]:
    """The rules an EOD run applies: the stored set, or the defaults when none is stored."""
    return stored_rules(repo) or list(DEFAULT_RULES)


def setup(repo: DuckDBRepository) -> dict[str, Any]:
    """The rule set as the Admin page shows it: the default row first, then any desk overrides,
    with the desks a row may name and the field descriptions."""
    rows = repo.load_stress_action_rules()
    by_scope = {r["scope"]: r for r in rows}
    rules = rules_for_run(repo)
    rules = sorted(rules, key=lambda r: (r.scope != DEFAULT_SCOPE, r.scope))
    return {
        "record": RECORD,
        "source": "stored" if rows else "defaults",
        "rules": [
            {
                **r.to_dict(),
                "updated_at": by_scope.get(r.scope, {}).get("updated_at"),
                "updated_by": by_scope.get(r.scope, {}).get("actor"),
            }
            for r in rules
        ],
        "desks": _desks(repo),
        "fields": FIELD_HELP,
    }


FIELD_HELP = {
    "scope": f"{DEFAULT_SCOPE} for every desk, or a desk id whose row replaces the default for that desk",
    "enabled": "False: the desk never acts in a path (its frozen and managed books are the same)",
    "stop_loss_var_multiple": "The desk acts from the day after its path loss reaches this multiple of its "
    "1-day VaR; every desk acts once the firm's path loss reaches the firm's stress-loss limit",
    "cut_fraction": "Share of every position sold once the desk acts (0.5 cuts gross by half)",
    "close_short_options": "Buy back short option positions in full",
    "hedge_delta": "Hedge the remaining delta every day with listed futures",
    "participation": "Largest share of a day's stressed volume the desk may trade",
    "stressed_volume_share": "Stressed volume as a share of normal volume",
    "stressed_spread_multiple": "Stressed bid-ask spread as a multiple of the normal one",
    "impact_coefficient": "Y in the square-root impact cost Y·σ·√(q/V) per unit traded",
}


def set_rules(
    repo: DuckDBRepository, actor: str, rules: list[dict[str, Any]], comment: str = ""
) -> dict[str, Any]:
    """Replace the rule set; validated and audited with the rules before and after."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    try:
        parsed = [ActionRule.from_dict(dict(r)) for r in rules]
    except (TypeError, ValueError) as e:
        raise WorkflowError(f"invalid action rule: {e}") from e
    errors = validate_rules(parsed, _desks(repo))
    if errors:
        raise WorkflowError("; ".join(errors))
    before = [r.to_dict() for r in rules_for_run(repo)]
    at = _now()
    repo.init_schema()
    repo.save_stress_action_rules(
        [{"scope": r.scope, "rule": r.to_dict(), "actor": actor, "updated_at": at} for r in parsed]
    )
    repo.save_audit_events(
        [
            AuditEvent.now(
                actor,
                "STRESS_ACTIONS_CHANGED",
                SUBJECT,
                before=before,
                after=[r.to_dict() for r in parsed],
                comment=comment,
            )
        ]
    )
    return setup(repo)


# --- the stage -----------------------------------------------------------------------------------
def run_stage(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    valuation: pd.DataFrame,
    liquidity_by_trade: pd.DataFrame,
    var_result: Any,
    limits: list[Limit],
    limit_results: list[Any],
    episodes: dict | None,
    rules: list[dict[str, Any]],
    book: QuadBook | None = None,
    space: PCASpace | None = None,
    workers: int | None = None,
    funding: FundingInputs | None = None,
    funding_rules: list[dict[str, Any]] | None = None,
    all_results: list[Any] | None = None,
) -> StressPathsResult:
    """The stage from a run's inputs; reuses the PCA space of the reverse stress when given. With
    ``funding`` the margin, collateral and funding chain (MR-024) walks the same paths."""
    if book is None:
        book = quad_book(pf, sens, list(pf.base.values))
    if space is None:
        space = build_space(pf, history, book, episodes, list(pf.priced_ids), PCA_HORIZON)
    desk_var = var_result.by(valuation, "desk_id").set_index("desk_id")["standalone_var"].to_dict()
    return run_stress_paths(
        pf,
        book,
        space,
        sens,
        history,
        valuation,
        liquidity_by_trade,
        {str(k): float(v) for k, v in desk_var.items()},
        targets_from_limits(limits, valuation),
        limit_results,
        [ActionRule.from_dict(dict(r)) for r in rules],
        workers=workers,
        funding=funding,
        funding_rules=FundingRules.from_list(funding_rules) if funding_rules else None,
        all_results=all_results,
    )


def persist_stress_paths(repo, rid: str, res: StressPathsResult) -> None:
    repo.save_run_frame(rid, "stress_paths", res.summary_table)
    repo.save_run_frame(rid, "stress_path_days", res.days)
    repo.save_run_frame(rid, "stress_path_desks", res.desks)
    repo.save_run_frame(rid, "stress_path_actions", res.actions)
    params = {k: v for k, v in res.parameters.items() if k != "rules"}
    params["rules_json"] = json.dumps(res.parameters.get("rules", []))
    repo.save_run_frame(rid, "stress_path_parameters", pd.DataFrame([params]))
    liq = res.liquidity
    if liq is not None:
        repo.save_run_frame(rid, "stress_liquidity", liq.summary_table)
        repo.save_run_frame(rid, "stress_liquidity_days", liq.days)
        repo.save_run_frame(rid, "stress_liquidity_instant", liq.instant)
        lp = {k: v for k, v in liq.parameters.items() if k != "rules"}
        lp["rules_json"] = json.dumps(liq.parameters.get("rules", []))
        repo.save_run_frame(rid, "stress_liquidity_parameters", pd.DataFrame([lp]))


# --- funding rules (MR-024) ----------------------------------------------------------------------
FUNDING_FIELD_HELP = {
    "cash_buffer": "Bank: cash held against calls on top of the HQLA, reporting currency (round 57 moves "
    "it to the resources table)",
    "hqla_haircut_level1": "Monetisation haircut on long government bonds in USD, EUR, GBP, JPY, CHF, "
    "CAD, AUD",
    "hqla_haircut_other": "Monetisation haircut on other government bonds",
    "im_collateral_haircut": "Haircut on the bonds we post as initial margin",
    "permanent_impact_share": "Share of a forced sale's square-root impact that stays in the price and "
    "re-marks what is still held",
    "spiral_max_rounds": "Most forced-sale rounds at one close before the firm is declared short",
    "ewma_lambda": "Decay of the volatility a CCP's procyclical margin reads",
    "ccp_margin_cap": "Most a CCP's margin can rise over today's on volatility (anti-procyclicality tools)",
    "threshold_we_post_multiplier": "Our CSA thresholds times this from day one (0: a downgrade removes "
    "them)",
    "repo_haircut_addon": "Extra haircut a repo lender asks when each of our repos rolls inside the horizon",
    "haircut_multiplier": "Collateral haircuts (CSA, IM collateral, HQLA) times this from day one",
    "ccp_procyclical": "CCP margin scales up with the path's realised volatility",
    "pb_margin_multiplier": "Fund: prime-broker margin times this (round 56 sets tightened terms)",
}


def stored_funding_rules(repo: DuckDBRepository) -> FundingRules | None:
    rows = repo.load_stress_funding_rules()
    return FundingRules.from_list([r["rule"] for r in rows]) if rows else None


def funding_rules_for_run(repo: DuckDBRepository) -> FundingRules:
    return stored_funding_rules(repo) or FundingRules()


def funding_setup(repo: DuckDBRepository) -> dict[str, Any]:
    rows = {r["scope"]: r for r in repo.load_stress_funding_rules()}
    rules = funding_rules_for_run(repo)
    return {
        "record": FUNDING_RECORD,
        "source": "stored" if rows else "defaults",
        "rules": [
            {
                **r,
                "updated_at": rows.get(r["scope"], {}).get("updated_at"),
                "updated_by": rows.get(r["scope"], {}).get("actor"),
            }
            for r in rules.to_list()
        ],
        "scenarios": list(FUNDING_SCENARIOS),
        "fields": FUNDING_FIELD_HELP,
    }


def set_funding_rules(
    repo: DuckDBRepository, actor: str, rules: list[dict[str, Any]], comment: str = ""
) -> dict[str, Any]:
    """Replace the funding rules (the resources row and the three scenarios); validated, audited."""
    if not actor or not actor.strip():
        raise WorkflowError("actor is required")
    rows = [dict(r) for r in rules]
    if not any(r.get("scope") == RESOURCES for r in rows):
        rows.insert(0, funding_rules_for_run(repo).to_list()[0])
    try:
        parsed = FundingRules.from_list(rows)
    except (TypeError, ValueError) as e:
        raise WorkflowError(f"invalid funding rule: {e}") from e
    errors = parsed.problems()
    if errors:
        raise WorkflowError("; ".join(errors))
    before = funding_rules_for_run(repo).to_list()
    at = _now()
    repo.init_schema()
    repo.save_stress_funding_rules(
        [{"scope": r["scope"], "rule": r, "actor": actor, "updated_at": at} for r in parsed.to_list()]
    )
    repo.save_audit_events(
        [
            AuditEvent.now(
                actor,
                "STRESS_FUNDING_CHANGED",
                FUNDING_SUBJECT,
                before=before,
                after=parsed.to_list(),
                comment=comment,
            )
        ]
    )
    return funding_setup(repo)


def funding_inputs(
    repo: DuckDBRepository,
    pf: Portfolio,
    sens: pd.DataFrame,
    valuation: pd.DataFrame,
    firm_id: str,
    rules: FundingRules,
    im_threshold: float,
    horizon: int,
) -> FundingInputs:
    """The firm's margin terms for the funding chain, in the order of ``pf.priced_ids``."""
    from novera.counterparty_risk.ccp import build_ccp_sets
    from novera.pricing.valuation import fx_to_reporting, value_trade
    from novera.regulatory.engine import simm_base_vols
    from novera.regulatory.simm import simm

    org = repo.load_organisation(firm_id)
    is_fund = org.firm.firm_type == "HEDGE_FUND"
    trades = list(pf.priced_ids)
    by_id = pf.by_id
    netting_sets, csas = repo.load_netting_sets()
    csa_by_id = {c.csa_id: c for c in csas}
    ns_csa = {n.netting_set_id: csa_by_id.get(n.csa_id) for n in netting_sets if n.csa_id}
    ccp = build_ccp_sets(pf.trades, org)
    ccp_of = ccp.by_trade
    market, reporting, res = pf.base, pf.reporting_currency, rules.resources
    regime, ns_arr, hqla = [], [], []
    for tid in trades:
        t = by_id[tid]
        ns = ccp_of.get(tid) or (t.netting_set_id or "")
        has_csa = ns in ns_csa and ns_csa[ns] is not None
        r = regime_of(
            t.product_type.value,
            t.direction.value,
            t.clearing.value,
            t.counterparty_id,
            has_csa,
            tid in ccp_of,
        )
        regime.append(r)
        ns_arr.append(ns if (tid in ccp_of or has_csa) else "")
        if r == "FUNDED" and t.product_type.value == "GOVERNMENT_BOND":
            hqla.append(
                res.hqla_haircut_level1 if t.currency in LEVEL1_CURRENCIES else res.hqla_haircut_other
            )
        else:
            hqla.append(np.nan)
    inp = FundingInputs(
        face="fund" if is_fund else "bank",
        as_of=pf.as_of,
        trades=trades,
        pv=np.array([pf.base_pv[t] for t in trades]),
        regime=np.array(regime),
        netting_set=np.array(ns_arr),
        hqla_haircut=np.array(hqla, dtype=float),
        im_threshold=im_threshold,
    )
    if is_fund:
        from novera.fund.engine import delta_equivalent
        from novera.fund.modules import pb_margin
        from novera.fund.reference import CROWDING, CROWDING_SURCHARGE, LISTED_PB, PB_MARGIN

        fund = repo.load_fund(org.firm.firm_id)
        if fund is None:
            raise RuntimeError(f"no fund metadata stored for {org.firm.firm_id}")
        notionals, underlyings = delta_equivalent(valuation, sens, by_id)
        margin = pb_margin(valuation, notionals, fund.nav, underlyings=underlyings)
        rate = []
        for tid in trades:
            t = by_id[tid]
            pb = (
                t.counterparty_id
                if t.counterparty_id in PB_MARGIN
                else LISTED_PB.get(t.counterparty_id, "PB_GS")
            )
            x = PB_MARGIN.get(pb, PB_MARGIN["PB_GS"]).get(t.product_type.value, 0.10)
            if CROWDING.get(str(underlyings.get(tid, "")), 0.0) > 0.7:
                x += CROWDING_SURCHARGE
            rate.append(x)
        inp.nav = float(fund.nav)
        inp.margin_total = float(margin["total_margin"])
        inp.margin_rate = np.array(rate)
        inp.exposure = notionals.reindex(trades).fillna(0.0).to_numpy(dtype=float)
        inp.linear = np.array([by_id[t].product_type.value in LINEAR_EXPOSURE for t in trades], dtype=float)
        return inp
    # Bank: CSA terms, SIMM-lite margin we post, repos and contractual flows.
    for ns, c in ns_csa.items():
        if c is not None:
            inp.csa[ns] = {
                "threshold_we_post": float(c.threshold_we_post),
                "threshold_they_post": float(c.threshold_they_post),
                "haircut": float(c.haircut),
            }
    val = valuation.assign(
        netting_set_id=valuation["trade_id"].map({t.trade_id: t.netting_set_id for t in pf.trades} | ccp_of)
    )
    im = simm(
        sens,
        val,
        simm_base_vols(market),
        csa_sets={n.netting_set_id for n in netting_sets if n.csa_id},
        threshold=im_threshold,
        ccp_sets=ccp.ids,
    ).by_netting_set
    for _, r in im.iterrows():
        if r["ccp"] or r["netting_set_id"] in inp.csa:
            inp.im_posted_model[str(r["netting_set_id"])] = float(r["im_posted_model"])
            inp.im_counterparty[str(r["netting_set_id"])] = str(r["counterparty_id"])
    inp.ccp_sets = frozenset(ccp.ids)
    for t in pf.trades:
        if isinstance(t.instrument, Repo) and t.trade_id in pf.base_pv:
            fx = fx_to_reporting(market, t.currency, reporting)
            sign = 1.0 if t.direction.value == "SELL" else -1.0
            inp.repos.append(
                {
                    "cash": sign * t.quantity * fx,
                    "haircut": float(t.instrument.haircut),
                    "end_date": t.instrument.end_date,
                }
            )
    dates = business_dates(pf.as_of, horizon)
    flows = np.zeros((len(trades), horizon))
    for i, tid in enumerate(trades):
        if inp.regime[i] in ("VM", "CSA"):
            continue  # a payment on a margined trade comes back as collateral
        t = by_id[tid]
        try:
            priced = value_trade(t, market, pf.as_of, pf.collateral)
        except Exception:  # noqa: BLE001 - an unpriced trade has no flows here
            continue
        fx = fx_to_reporting(market, priced.currency, reporting)
        for cf in priced.cashflows:
            if cf.kind not in CONTRACTUAL_KINDS or cf.pay_date <= pf.as_of or cf.pay_date > dates[-1]:
                continue
            d = next((k for k, bd in enumerate(dates) if bd >= cf.pay_date), None)
            if d is not None:
                flows[i, d] += cf.amount * fx
    inp.flows = flows
    return inp
