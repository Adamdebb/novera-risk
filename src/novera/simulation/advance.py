"""Advance the simulated world by one business day without changing the past.

The next day's market is a historical bootstrap: one past day's factor moves (chosen by a
seed derived from the new date) applied to the latest snapshot. The portfolio evolves as
in ``evolve_portfolio``. Both are appended to storage, so earlier runs stay reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from novera.domain import Organisation
from novera.domain.snapshots import PortfolioSnapshot
from novera.market_data.history import MarketHistory
from novera.market_data.snapshot import MarketSnapshot
from novera.risk.scenarios import shock_type_of
from novera.simulation.fund import FUND_TEMPLATE, build_fund_counterparties
from novera.simulation.market_data import business_days_after
from novera.simulation.organisation import CounterpartyUniverse, build_counterparty_universe
from novera.simulation.trades import BANK_TEMPLATE, Injection, Template, evolve_portfolio
from novera.storage.duckdb_repository import DuckDBRepository


@dataclass
class AdvanceResult:
    business_date: date
    market_snapshot_id: str
    portfolio_snapshot_id: str
    bootstrap_from: date
    changes: list[str]


def next_business_day(repo: DuckDBRepository) -> date:
    msnaps = repo.list_market_snapshots()
    if not msnaps:
        raise RuntimeError("no market snapshots stored; run the simulator first")
    return business_days_after(msnaps[-1][1], 1)[0]


def face_of(org: Organisation) -> tuple[Template, CounterpartyUniverse]:
    """The trade template and counterparty universe the simulator used for this organisation:
    the fund's strategies and prime brokers, or the bank's desks and dealers. New business on
    an advanced day is drawn from the same template as day one."""
    if org.firm.firm_type == "HEDGE_FUND":
        return FUND_TEMPLATE, build_fund_counterparties(org)
    return BANK_TEMPLATE, build_counterparty_universe(org)


def advance_business_day(
    repo: DuckDBRepository, firm_id: str = "GMB", new_trade_share: float = 0.03
) -> AdvanceResult:
    msnaps = repo.list_market_snapshots()
    last_id, last_date, _ = msnaps[-1]
    last = repo.load_market_snapshot(last_id)
    new_date = business_days_after(last_date, 1)[0]
    hist = MarketHistory.from_long(repo.load_market_history())
    universe = {f.factor_id: f for f in repo.load_risk_factors()}

    # Bootstrap one past day's moves. Deterministic per date, never the last day itself.
    rng = np.random.default_rng(int(new_date.strftime("%Y%m%d")))
    wide = hist.wide
    i = int(rng.integers(1, len(wide) - 1))
    prev_row, cur_row = wide.iloc[i - 1], wide.iloc[i]
    values: dict[str, float] = {}
    for fid, v in last.values.items():
        if fid not in wide.columns or pd.isna(prev_row[fid]) or pd.isna(cur_row[fid]):
            values[fid] = v
            continue
        if shock_type_of(fid, universe) == "ABSOLUTE":
            values[fid] = float(v + (cur_row[fid] - prev_row[fid]))
        else:
            values[fid] = float(v * (cur_row[fid] / prev_row[fid])) if prev_row[fid] else v
    # Restore any factor missing from the last snapshot (planted problems do not persist).
    for fid in wide.columns:
        if fid not in values and not pd.isna(wide.iloc[-1][fid]):
            base = float(wide.iloc[-1][fid])
            if shock_type_of(fid, universe) == "ABSOLUTE":
                values[fid] = base + float(cur_row[fid] - prev_row[fid])
            else:
                values[fid] = base * float(cur_row[fid] / prev_row[fid]) if prev_row[fid] else base
    market = MarketSnapshot(as_of=new_date, values=values, source="SIM_BOOTSTRAP")
    market_id = repo.save_market_snapshot(market)
    repo.save_market_history(
        pd.DataFrame(
            {"as_of": [new_date] * len(values), "factor_id": list(values), "value": list(values.values())}
        )
    )

    # Portfolio: evolve from the latest snapshot; no further unwinds after the first evolution.
    psnaps = repo.list_portfolio_snapshots()
    prev_pf: PortfolioSnapshot = repo.load_portfolio_snapshot(psnaps[-1][0])
    org = repo.load_organisation(firm_id)
    template, cp = face_of(org)
    hist2 = MarketHistory.from_long(repo.load_market_history())
    injections: list[Injection] = []
    nxt, changes = evolve_portfolio(
        prev_pf,
        new_date,
        org,
        cp,
        injections,
        hist2,
        seed=int(new_date.strftime("%Y%m%d")) % 100_000,
        new_trade_share=new_trade_share,
        template=template,
    )
    pf_id = repo.save_portfolio_snapshot(nxt)
    return AdvanceResult(new_date, market_id, pf_id, wide.index[i], changes)
