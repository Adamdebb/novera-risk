from collections import Counter
from datetime import date

import pytest

from novera.domain import ClearingType, ProductType, TradeStatus, Venue
from novera.simulation import (
    TradeGeneratorConfig,
    build_counterparty_universe,
    build_global_macro_bank,
    generate_portfolio,
)

BD = date(2026, 9, 11)


@pytest.fixture(scope="module")
def org():
    return build_global_macro_bank()


@pytest.fixture(scope="module")
def cp(org):
    return build_counterparty_universe(org)


@pytest.fixture(scope="module")
def generated(org, cp):
    return generate_portfolio(org, cp, TradeGeneratorConfig(business_date=BD, n_trades=600, seed=7))


def test_organisation_shape(org) -> None:
    assert len(org.legal_entities) == 3
    assert len(org.desks) == 14
    assert len(org.books) >= 25
    assert len(org.traders) == 28
    assert {d.asset_class for d in org.desks} == {
        "RATES",
        "FX",
        "EQUITY",
        "CREDIT",
        "COMMODITY",
        "DIGITAL_ASSET",
    }


def test_counterparty_universe(org, cp) -> None:
    assert len(cp.counterparties) >= 25
    bilateral = cp.bilateral
    # Every bilateral counterparty has a netting set with every legal entity.
    assert len(cp.netting_sets) == len(bilateral) * len(org.legal_entities)
    uncollateralised = {ns.counterparty_id for ns in cp.netting_sets if ns.csa_id is None}
    assert uncollateralised == {"CORP_ENERGY", "CORP_AIR", "SOV_EM"}
    csa_ids = {c.csa_id for c in cp.csas}
    assert all(ns.csa_id in csa_ids for ns in cp.netting_sets if ns.csa_id)


def test_generator_is_reproducible(org, cp) -> None:
    cfg = TradeGeneratorConfig(business_date=BD, n_trades=200, seed=3)
    a = generate_portfolio(org, cp, cfg).snapshot
    b = generate_portfolio(org, cp, cfg).snapshot
    assert a.snapshot_id == b.snapshot_id
    c = generate_portfolio(org, cp, TradeGeneratorConfig(business_date=BD, n_trades=200, seed=4))
    assert c.snapshot.snapshot_id != a.snapshot_id


def test_portfolio_covers_all_products_and_desks(generated, org) -> None:
    snap = generated.snapshot
    products = {t.product_type for t in snap.trades}
    assert products == set(ProductType), set(ProductType) - products
    desks_used = {
        org.desk(org.book(t.book_id).desk_id).desk_id for t in snap.trades if t.book_id != "GHOST_BOOK"
    }
    assert desks_used == {d.desk_id for d in org.desks}
    assert 600 <= len(snap) <= 640


def test_referential_integrity_except_injected(generated, org, cp) -> None:
    snap = generated.snapshot
    invalid = {tid for i in generated.injections if i.name == "invalid_trades" for tid in i.trade_ids}
    book_ids = {b.book_id for b in org.books}
    cpty_ids = {c.counterparty_id for c in cp.counterparties}
    ns_ids = {n.netting_set_id for n in cp.netting_sets}
    for t in snap.trades:
        if t.trade_id in invalid:
            continue
        assert t.book_id in book_ids, t.trade_id
        assert t.counterparty_id in cpty_ids, t.trade_id
        if t.venue is Venue.OTC and t.clearing is ClearingType.BILATERAL:
            assert t.netting_set_id in ns_ids, t.trade_id
            ns = next(n for n in cp.netting_sets if n.netting_set_id == t.netting_set_id)
            assert ns.legal_entity_id == org.book(t.book_id).legal_entity_id
        if t.venue is Venue.LISTED and t.product_type is not ProductType.MUTUAL_FUND:
            assert t.clearing is ClearingType.EXCHANGE
        if t.product_type is ProductType.MUTUAL_FUND:
            # Dealt at NAV with the manager's transfer agent: bilateral settlement, no netting set.
            assert t.clearing is ClearingType.BILATERAL and t.netting_set_id is None
        assert t.status is TradeStatus.LIVE


def test_injections_present(generated) -> None:
    names = {i.name for i in generated.injections}
    assert names == {
        "usd_10y_concentration",
        "illiquid_brent",
        "btc_exposure",
        "wrong_way_sovereign",
        "wrong_way_credit",
        "invalid_trades",
    }
    by_id = {t.trade_id: t for t in generated.snapshot.trades}
    conc = next(i for i in generated.injections if i.name == "usd_10y_concentration")
    for tid in conc.trade_ids:
        t = by_id[tid]
        assert t.counterparty_id == "BANK_A" and t.quantity == 400e6
        assert t.instrument.maturity_date.year - t.instrument.effective_date.year == 10
    cpty_counts = Counter(
        t.counterparty_id for t in generated.snapshot.trades if t.clearing is ClearingType.BILATERAL
    )
    assert cpty_counts.most_common(1)[0][0] == "BANK_A"


def test_no_injection_option(org, cp) -> None:
    g = generate_portfolio(
        org, cp, TradeGeneratorConfig(business_date=BD, n_trades=50, seed=1, inject_problems=False)
    )
    assert g.injections == [] and len(g.snapshot) == 50
