from datetime import date

import pytest
from pydantic import TypeAdapter, ValidationError

from novera.domain import (
    AssetClass,
    BuySell,
    ClearingType,
    FXForward,
    HierarchyLevel,
    Instrument,
    InterestRateSwap,
    Limit,
    LimitScope,
    LimitType,
    Organisation,
    PortfolioSnapshot,
    Trade,
    Venue,
)


def test_instrument_union_round_trips(swap_trade: Trade) -> None:
    raw = swap_trade.instrument.model_dump(mode="json")
    parsed = TypeAdapter(Instrument).validate_python(raw)
    assert isinstance(parsed, InterestRateSwap)
    assert parsed == swap_trade.instrument
    assert parsed.asset_class is AssetClass.RATES
    assert parsed.venue is Venue.OTC


def test_fx_currency_must_be_quote() -> None:
    with pytest.raises(ValidationError, match="quote currency"):
        FXForward(instrument_id="x", currency="EUR", pair="EUR/USD",
                  settlement_date=date(2026, 12, 15), forward_rate=1.1)
    ok = FXForward(instrument_id="x", currency="USD", pair="EUR/USD",
                   settlement_date=date(2026, 12, 15), forward_rate=1.1)
    assert ok.base_currency == "EUR" and ok.quote_currency == "USD"


def test_swap_requires_side(swap_trade: Trade) -> None:
    with pytest.raises(ValidationError, match="swap_side"):
        swap_trade.model_copy(update={"swap_side": None}).model_validate(
            swap_trade.model_dump() | {"swap_side": None}
        )


def test_bilateral_otc_requires_netting_set(swap_trade: Trade) -> None:
    data = swap_trade.model_dump() | {"netting_set_id": None}
    with pytest.raises(ValidationError, match="netting_set_id"):
        Trade.model_validate(data)


def test_otc_cannot_be_exchange_cleared(swap_trade: Trade) -> None:
    data = swap_trade.model_dump() | {"clearing": ClearingType.EXCHANGE}
    with pytest.raises(ValidationError, match="exchange"):
        Trade.model_validate(data)


def test_signed_quantity(bond_trade: Trade, equity_trade: Trade) -> None:
    assert bond_trade.direction is BuySell.SELL and bond_trade.signed_quantity == -50_000_000
    assert equity_trade.signed_quantity == 120_000


def test_organisation_integrity(organisation: Organisation) -> None:
    h = organisation.hierarchy_for_book("EU_INDEX")
    assert h == {
        "book_id": "EU_INDEX", "desk_id": "INDEX_EQ", "business_id": "MACRO",
        "firm_id": "GMB", "legal_entity_id": "GMB_LN", "desk_asset_class": "EQUITY",
        "region": "EMEA",
    }
    bad = organisation.model_dump()
    bad["books"][0]["desk_id"] = "NOPE"
    with pytest.raises(ValidationError, match="unknown desk"):
        Organisation.model_validate(bad)


def test_snapshot_id_is_content_hash(snapshot: PortfolioSnapshot) -> None:
    same = PortfolioSnapshot(business_date=snapshot.business_date, trades=snapshot.trades[::-1])
    assert same.snapshot_id == snapshot.snapshot_id, "order must not matter"
    fewer = PortfolioSnapshot(business_date=snapshot.business_date, trades=snapshot.trades[:2])
    assert fewer.snapshot_id != snapshot.snapshot_id
    other_day = PortfolioSnapshot(business_date=date(2026, 9, 12), trades=snapshot.trades)
    assert other_day.snapshot_id != snapshot.snapshot_id


def test_limit_effectiveness() -> None:
    lim = Limit(
        limit_id="L1", limit_type=LimitType.DV01,
        scope=LimitScope(level=HierarchyLevel.DESK, entity_id="USD_RATES", tenor_bucket="10Y"),
        amount=5_000_000, owner="Head of Rates", effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
    )
    assert lim.is_effective(date(2026, 9, 11))
    assert not lim.is_effective(date(2027, 1, 1))
    with pytest.raises(ValidationError):
        Limit.model_validate(lim.model_dump() | {"effective_to": date(2025, 1, 1)})
