from datetime import date

import pytest

from novera.domain import (
    CSA,
    Counterparty,
    HierarchyLevel,
    Limit,
    LimitScope,
    LimitType,
    NettingSet,
)
from novera.domain.organisation import Organisation
from novera.domain.snapshots import PortfolioSnapshot
from novera.storage.duckdb_repository import DuckDBRepository


@pytest.fixture
def repo(tmp_path):
    with DuckDBRepository(tmp_path / "test.duckdb") as r:
        r.init_schema()
        r.init_schema()  # idempotent
        yield r


def test_organisation_round_trip(repo: DuckDBRepository, organisation: Organisation) -> None:
    repo.save_organisation(organisation)
    loaded = repo.load_organisation("GMB")
    assert loaded == organisation
    with pytest.raises(KeyError):
        repo.load_organisation("NOPE")


def test_counterparty_and_netting_round_trip(
    repo: DuckDBRepository, counterparty: Counterparty, netting_set: NettingSet, csa: CSA
) -> None:
    repo.save_counterparties([counterparty])
    repo.save_netting_sets([netting_set], [csa])
    assert repo.load_counterparties() == [counterparty]
    assert repo.load_netting_sets() == ([netting_set], [csa])


def test_limits_effective_filter(repo: DuckDBRepository) -> None:
    scope = LimitScope(level=HierarchyLevel.FIRM, entity_id="GMB")
    live = Limit(
        limit_id="L_VAR",
        limit_type=LimitType.VAR,
        scope=scope,
        amount=50e6,
        owner="CRO",
        effective_from=date(2026, 1, 1),
    )
    expired = Limit(
        limit_id="L_OLD",
        limit_type=LimitType.VAR,
        scope=scope,
        amount=40e6,
        owner="CRO",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),
    )
    repo.save_limits([live, expired])
    assert {lim.limit_id for lim in repo.load_limits()} == {"L_VAR", "L_OLD"}
    assert [lim.limit_id for lim in repo.load_limits(on=date(2026, 9, 11))] == ["L_VAR"]


def test_snapshot_round_trip_and_immutability(repo: DuckDBRepository, snapshot: PortfolioSnapshot) -> None:
    sid = repo.save_portfolio_snapshot(snapshot)
    assert repo.save_portfolio_snapshot(snapshot) == sid  # second save is a no-op
    loaded = repo.load_portfolio_snapshot(sid)
    assert loaded.snapshot_id == sid
    assert set(t.trade_id for t in loaded.trades) == set(t.trade_id for t in snapshot.trades)
    assert loaded.trades[0].instrument.asset_class is not None
    assert repo.list_portfolio_snapshots() == [(sid, snapshot.business_date, 3)]
    with pytest.raises(KeyError):
        repo.load_portfolio_snapshot("missing")
