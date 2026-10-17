"""Reverse stress (MR-021) and the stressed PCA Monte Carlo (MR-022): one PCA space built once
for both, then each engine, for the run's effective stress-loss limits (decision round 50)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from novera.domain.limits import Limit
from novera.market_data.history import MarketHistory
from novera.risk.pca_space import HORIZON, build_space, quad_book
from novera.risk.revaluation import Portfolio
from novera.risk.reverse_stress import ReverseStressResult, run_reverse_stress, targets_from_limits
from novera.risk.stress_monte_carlo import StressMonteCarloResult, run_stress_monte_carlo

REVERSE_STRESS_TABLES = (
    "reverse_stress",
    "reverse_stress_neighbours",
    "reverse_stress_moves",
    "pca_space",
    "pca_tail",
)
STRESS_MC_TABLES = ("stress_mc", "stress_mc_quantiles", "stress_mc_targets", "stress_mc_parameters")


@dataclass
class PCAStress:
    reverse: ReverseStressResult | None
    monte_carlo: StressMonteCarloResult | None
    seconds: dict[str, float]
    space: dict[str, object] | None = None

    def findings(self) -> list:
        """A MINOR data-quality finding when no component count up to the cap reproduces the firm's
        worst 1% of historical moves (the PCA space smooths the tail away)."""
        from novera.data_quality.checks import Finding
        from novera.risk.pca_space import TAIL_TARGET

        sp = self.space
        if not sp or float(sp["tail_capture"]) >= TAIL_TARGET:  # type: ignore[arg-type]
            return []
        return [
            Finding(
                "PCA_TAIL_CAPTURE_LOW",
                "MINOR",
                f"The PCA space of {sp['components']} components reproduces "
                f"{float(sp['tail_capture']):.0%} of "
                f"the book's worst 1% of historical moves (target {TAIL_TARGET:.0%}); reverse stress and the "
                "stressed Monte Carlo understate the idiosyncratic tail (MR-021)",
                "pca",
                owner="Market Risk",
            )
        ]


def run_pca_stress(
    pf: Portfolio,
    sens: pd.DataFrame,
    history: MarketHistory,
    valuation: pd.DataFrame,
    limits: list[Limit],
    episodes: dict[str, tuple[date, date]] | None,
    reverse: bool = True,
    monte_carlo: bool = True,
) -> PCAStress:
    import time

    secs: dict[str, float] = {}
    t = time.perf_counter()
    book = quad_book(pf, sens, list(pf.base.values))
    space = build_space(pf, history, book, episodes, list(pf.priced_ids), HORIZON)
    secs["pca_space"] = round(time.perf_counter() - t, 3)
    targets = targets_from_limits(limits, valuation)
    rev = mc = None
    if reverse:
        t = time.perf_counter()
        rev = run_reverse_stress(pf, book, space, targets)
        secs["reverse_stress"] = round(time.perf_counter() - t, 3)
    if monte_carlo:
        t = time.perf_counter()
        v = valuation.drop_duplicates("trade_id")
        priced = set(pf.priced_ids)
        scopes = {
            str(d): [x for x in g["trade_id"] if x in priced] for d, g in v.groupby("desk_id") if len(g)
        }
        mc = run_stress_monte_carlo(pf, book, space, scopes, targets)
        secs["stress_monte_carlo"] = round(time.perf_counter() - t, 3)
    return PCAStress(rev, mc, secs, space.to_dict())


def persist_reverse_stress(repo, rid: str, res: ReverseStressResult) -> None:
    repo.save_run_frame(rid, "reverse_stress", res.solutions)
    repo.save_run_frame(rid, "reverse_stress_neighbours", res.neighbours)
    repo.save_run_frame(rid, "reverse_stress_moves", res.moves)
    repo.save_run_frame(rid, "pca_space", pd.DataFrame([res.space]))
    repo.save_run_frame(rid, "pca_tail", res.tail)


def persist_stress_mc(repo, rid: str, res: StressMonteCarloResult) -> None:
    repo.save_run_frame(rid, "stress_mc", res.summary_table)
    repo.save_run_frame(rid, "stress_mc_quantiles", res.quantiles)
    repo.save_run_frame(rid, "stress_mc_targets", res.targets)
    repo.save_run_frame(rid, "stress_mc_parameters", pd.DataFrame([res.parameters]))
