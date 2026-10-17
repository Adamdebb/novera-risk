"""Concentration and liquidation stress (MR-025, decision round 53): the EOD stage and its tables.

The execution limits (participation, stressed volume, spread multiple, impact) are the default row of
the stress-path action rules the run recorded (MR-023), so a re-run reproduces the run."""

from __future__ import annotations

from typing import Any

import pandas as pd

from novera.risk.liquidation_stress import LiquidationResult, run_liquidation_stress
from novera.risk.pca_space import HORIZON as PCA_HORIZON
from novera.risk.pca_space import PCASpace, QuadBook, build_space, quad_book
from novera.risk.stress_paths import DEFAULT_SCOPE, ActionRule

LIQUIDATION_TABLES = (
    "liquidation_markets",
    "liquidation_issuers",
    "liquidation_sectors",
    "liquidation_stress",
    "liquidation_desks",
    "liquidation_positions",
    "liquidation_parameters",
)


def default_rule(rules: list[dict[str, Any]] | None) -> ActionRule:
    for r in rules or []:
        if r.get("scope") == DEFAULT_SCOPE:
            return ActionRule.from_dict(dict(r))
    return ActionRule()


def run_stage(
    pf: Any,
    sens: pd.DataFrame,
    history: Any,
    valuation: pd.DataFrame,
    liquidity_by_trade: pd.DataFrame,
    results: list[Any],
    limit_results: list[Any],
    rules: list[dict[str, Any]] | None,
    episodes: dict | None,
    book: QuadBook | None = None,
    space: PCASpace | None = None,
) -> LiquidationResult:
    from novera.fund.engine import delta_equivalent

    if book is None:
        book = quad_book(pf, sens, list(pf.base.values))
    if space is None:
        space = build_space(pf, history, book, episodes, list(pf.priced_ids), PCA_HORIZON)
    exposures, _ = delta_equivalent(valuation, sens, pf.by_id)
    rule = default_rule(rules)
    return run_liquidation_stress(
        pf,
        book,
        space,
        history,
        valuation,
        liquidity_by_trade,
        exposures,
        results,
        {r.scenario.scenario_id for r in limit_results},
        rule.participation,
        rule.stressed_volume_share,
        rule.stressed_spread_multiple,
        rule.impact_coefficient,
    )


def persist_liquidation(repo: Any, rid: str, res: LiquidationResult) -> None:
    repo.save_run_frame(rid, "liquidation_markets", res.markets)
    repo.save_run_frame(rid, "liquidation_issuers", res.issuers)
    repo.save_run_frame(rid, "liquidation_sectors", res.sectors)
    repo.save_run_frame(rid, "liquidation_stress", res.scenarios)
    repo.save_run_frame(rid, "liquidation_desks", res.desks)
    repo.save_run_frame(rid, "liquidation_positions", res.positions)
    repo.save_run_frame(rid, "liquidation_parameters", pd.DataFrame([res.parameters]))
