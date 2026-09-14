"""The simulated trading organisation.

Aggregation hierarchy: book -> desk -> business -> firm. Legal entity is carried by the
book and is a cross-cutting dimension, as in a real bank where one desk books into
several entities.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novera.domain.enums import AssetClass


class _Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Firm(_Entity):
    firm_id: str
    name: str
    firm_type: str = Field(description="INVESTMENT_BANK or HEDGE_FUND")


class LegalEntity(_Entity):
    legal_entity_id: str
    firm_id: str
    name: str
    jurisdiction: str = Field(description="ISO 3166 alpha-2, e.g. US, GB, DE")
    functional_currency: str = Field(pattern=r"^[A-Z]{3}$")


class Business(_Entity):
    business_id: str
    firm_id: str
    name: str = Field(description="e.g. Global Markets, Macro, Equities")


class Desk(_Entity):
    desk_id: str
    business_id: str
    name: str = Field(description="e.g. USD Rates, G10 FX, Index Equity")
    asset_class: AssetClass
    region: str = Field(description="e.g. AMER, EMEA, APAC")
    head: str = ""


class Book(_Entity):
    book_id: str
    desk_id: str
    legal_entity_id: str
    name: str = Field(description="e.g. USD Macro RV")
    strategy: str = ""


class Trader(_Entity):
    trader_id: str
    desk_id: str
    name: str


class Organisation(BaseModel):
    """A complete organisation with referential integrity enforced on construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    firm: Firm
    legal_entities: tuple[LegalEntity, ...]
    businesses: tuple[Business, ...]
    desks: tuple[Desk, ...]
    books: tuple[Book, ...]
    traders: tuple[Trader, ...]

    @model_validator(mode="after")
    def _integrity(self) -> Organisation:
        le = {e.legal_entity_id for e in self.legal_entities}
        bu = {b.business_id for b in self.businesses}
        dk = {d.desk_id for d in self.desks}
        for e in self.legal_entities:
            if e.firm_id != self.firm.firm_id:
                raise ValueError(f"legal entity {e.legal_entity_id} not in firm")
        for b in self.businesses:
            if b.firm_id != self.firm.firm_id:
                raise ValueError(f"business {b.business_id} not in firm")
        for d in self.desks:
            if d.business_id not in bu:
                raise ValueError(f"desk {d.desk_id} references unknown business")
        for k in self.books:
            if k.desk_id not in dk:
                raise ValueError(f"book {k.book_id} references unknown desk")
            if k.legal_entity_id not in le:
                raise ValueError(f"book {k.book_id} references unknown legal entity")
        for t in self.traders:
            if t.desk_id not in dk:
                raise ValueError(f"trader {t.trader_id} references unknown desk")
        ids = [k.book_id for k in self.books]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate book_id")
        return self

    def book(self, book_id: str) -> Book:
        return self._by_id(self.books, "book_id", book_id)

    def desk(self, desk_id: str) -> Desk:
        return self._by_id(self.desks, "desk_id", desk_id)

    def business(self, business_id: str) -> Business:
        return self._by_id(self.businesses, "business_id", business_id)

    def legal_entity(self, legal_entity_id: str) -> LegalEntity:
        return self._by_id(self.legal_entities, "legal_entity_id", legal_entity_id)

    def hierarchy_for_book(self, book_id: str) -> dict[str, str]:
        """All hierarchy keys a trade in this book inherits (used to enrich positions)."""
        book = self.book(book_id)
        desk = self.desk(book.desk_id)
        business = self.business(desk.business_id)
        return {
            "book_id": book.book_id,
            "desk_id": desk.desk_id,
            "business_id": business.business_id,
            "firm_id": self.firm.firm_id,
            "legal_entity_id": book.legal_entity_id,
            "desk_asset_class": desk.asset_class.value,
            "region": desk.region,
        }

    @staticmethod
    def _by_id(items: tuple, key: str, value: str):  # type: ignore[no-untyped-def]
        for it in items:
            if getattr(it, key) == value:
                return it
        raise KeyError(f"{key}={value!r} not found")
