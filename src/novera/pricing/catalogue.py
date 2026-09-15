"""The product catalogue: what the platform can price, with which model, under which
methodology record, and what defines an instrument of each type.

This is reference data read from code, not from a run. ``tests/test_api.py`` checks that
every product type has an entry, that the model name matches what the pricer reports, and
that the methodology record exists.
"""

from __future__ import annotations

import enum
import types
import typing
from dataclasses import dataclass
from typing import Any

from novera.domain import instruments as _instruments
from novera.domain.enums import PRODUCT_ASSET_CLASS, AssetClass, ProductType
from novera.domain.instruments import VENUE_BY_PRODUCT, InstrumentBase
from novera.pricing.breadth import (
    COMMODITY_OPTION_MODEL_VERSION,
    EXOTIC_MODEL_VERSION,
    FUND_LOOKTHROUGH_MODEL_VERSION,
    IR_FUTURE_MODEL_VERSION,
    REPO_MODEL_VERSION,
    SWAPTION_MODEL_VERSION,
)
from novera.pricing.commodity import COMMODITY_MODEL_VERSION
from novera.pricing.credit import CDS_MODEL_VERSION
from novera.pricing.crypto import CRYPTO_MODEL_VERSION
from novera.pricing.equity import EQUITY_MODEL_VERSION, EQUITY_OPTION_MODEL_VERSION
from novera.pricing.fx import FX_FORWARD_MODEL_VERSION, FX_OPTION_MODEL_VERSION
from novera.pricing.rates import BOND_MODEL_VERSION, SWAP_MODEL_VERSION

ASSET_CLASS_NAMES: dict[AssetClass, str] = {
    AssetClass.RATES: "Rates",
    AssetClass.FX: "FX",
    AssetClass.EQUITY: "Equity",
    AssetClass.CREDIT: "Credit",
    AssetClass.COMMODITY: "Commodities",
    AssetClass.DIGITAL_ASSET: "Digital assets",
}


@dataclass(frozen=True)
class ProductSpec:
    product_type: ProductType
    name: str
    model: str
    """Model name the pricer writes on every valuation row."""
    model_label: str
    model_version: str
    methodology: str
    methodology_title: str


PRODUCT_CATALOGUE: dict[ProductType, ProductSpec] = {
    spec.product_type: spec
    for spec in (
        ProductSpec(
            ProductType.GOVERNMENT_BOND,
            "Government bond",
            "bond_discounting",
            "Discounting off the government curve",
            BOND_MODEL_VERSION,
            "PR-001",
            "Government bond valuation",
        ),
        ProductSpec(
            ProductType.INTEREST_RATE_SWAP,
            "Interest-rate swap",
            "swap_single_curve",
            "Single-curve discounting and forwarding",
            SWAP_MODEL_VERSION,
            "PR-002",
            "Interest-rate swap valuation",
        ),
        ProductSpec(
            ProductType.REPO,
            "Repo and reverse repo",
            "repo_cash_leg",
            "Cash leg off the zero curve",
            REPO_MODEL_VERSION,
            "PR-010",
            "Repo and reverse repo valuation",
        ),
        ProductSpec(
            ProductType.INTEREST_RATE_FUTURE,
            "Interest-rate future",
            "ir_future_forward",
            "Curve forward, no convexity adjustment",
            IR_FUTURE_MODEL_VERSION,
            "PR-011",
            "Interest-rate future valuation",
        ),
        ProductSpec(
            ProductType.SWAPTION,
            "European swaption",
            "swaption_bachelier",
            "Bachelier on a normal volatility cube",
            SWAPTION_MODEL_VERSION,
            "PR-012",
            "European swaption valuation",
        ),
        ProductSpec(
            ProductType.FX_SPOT,
            "FX spot",
            "fx_spot_mtm",
            "Mark to market at spot",
            FX_FORWARD_MODEL_VERSION,
            "PR-003",
            "FX spot and forward valuation",
        ),
        ProductSpec(
            ProductType.FX_FORWARD,
            "FX forward",
            "fx_forward_cip",
            "Covered interest parity",
            FX_FORWARD_MODEL_VERSION,
            "PR-003",
            "FX spot and forward valuation",
        ),
        ProductSpec(
            ProductType.FX_OPTION,
            "FX vanilla option",
            "fx_option_garman_kohlhagen",
            "Garman-Kohlhagen",
            FX_OPTION_MODEL_VERSION,
            "PR-004",
            "FX vanilla option valuation",
        ),
        ProductSpec(
            ProductType.CASH_EQUITY,
            "Cash equity",
            "equity_mtm",
            "Mark to market",
            EQUITY_MODEL_VERSION,
            "PR-005",
            "Cash equity and index future valuation",
        ),
        ProductSpec(
            ProductType.EQUITY_INDEX_FUTURE,
            "Equity index future",
            "index_future_carry",
            "Cost of carry",
            EQUITY_MODEL_VERSION,
            "PR-005",
            "Cash equity and index future valuation",
        ),
        ProductSpec(
            ProductType.EQUITY_OPTION,
            "Equity vanilla option",
            "equity_option_black_scholes",
            "Black-Scholes",
            EQUITY_OPTION_MODEL_VERSION,
            "PR-006",
            "Equity vanilla option valuation",
        ),
        ProductSpec(
            ProductType.ETF,
            "ETF",
            "fund_lookthrough",
            "NAV by look-through to constituents",
            FUND_LOOKTHROUGH_MODEL_VERSION,
            "PR-015",
            "ETF and mutual-fund valuation by look-through",
        ),
        ProductSpec(
            ProductType.MUTUAL_FUND,
            "Mutual fund",
            "fund_lookthrough",
            "NAV by look-through to constituents",
            FUND_LOOKTHROUGH_MODEL_VERSION,
            "PR-015",
            "ETF and mutual-fund valuation by look-through",
        ),
        ProductSpec(
            ProductType.EQUITY_EXOTIC,
            "Equity barrier and digital option",
            "exotic_closed_form_bs",
            "Reiner-Rubinstein barriers, cash-or-nothing digitals",
            EXOTIC_MODEL_VERSION,
            "PR-016",
            "Equity barrier and digital option valuation",
        ),
        ProductSpec(
            ProductType.CDS_INDEX,
            "CDS index",
            "cds_flat_hazard",
            "Flat hazard rate (ISDA standard model, simplified)",
            CDS_MODEL_VERSION,
            "PR-008",
            "CDS index valuation",
        ),
        ProductSpec(
            ProductType.CDS_SINGLE_NAME,
            "Single-name CDS",
            "cds_flat_hazard",
            "Flat hazard rate (shared with the index model)",
            CDS_MODEL_VERSION,
            "PR-013",
            "Single-name CDS valuation",
        ),
        ProductSpec(
            ProductType.COMMODITY_FUTURE,
            "Commodity future",
            "commodity_future_curve",
            "Mark off the futures curve",
            COMMODITY_MODEL_VERSION,
            "PR-007",
            "Commodity future valuation",
        ),
        ProductSpec(
            ProductType.COMMODITY_OPTION,
            "Commodity option",
            "commodity_option_black76",
            "Black 76 on the curve price",
            COMMODITY_OPTION_MODEL_VERSION,
            "PR-014",
            "Commodity option valuation",
        ),
        ProductSpec(
            ProductType.CRYPTO_SPOT,
            "Crypto spot (BTC, ETH)",
            "crypto_mtm",
            "Mark to market",
            CRYPTO_MODEL_VERSION,
            "PR-009",
            "Digital-asset spot valuation",
        ),
    )
}


def instrument_classes() -> dict[ProductType, type[InstrumentBase]]:
    """The instrument model for each product type, found on the discriminated union."""
    out: dict[ProductType, type[InstrumentBase]] = {}
    for name in dir(_instruments):
        cls = getattr(_instruments, name)
        if (
            isinstance(cls, type)
            and issubclass(cls, InstrumentBase)
            and cls is not InstrumentBase
            and "product_type" in cls.model_fields
            and not name.startswith("_")
        ):
            out[cls.model_fields["product_type"].default] = cls
    return out


def _type_name(ann: Any) -> str:
    origin = typing.get_origin(ann)
    if origin is typing.Annotated:
        return _type_name(typing.get_args(ann)[0])
    if origin is typing.Literal:
        return "literal"
    if origin in (types.UnionType, typing.Union):
        return " | ".join(_type_name(a) for a in typing.get_args(ann))
    if origin is not None:
        inner = ", ".join(_type_name(a) for a in typing.get_args(ann))
        return f"{origin.__name__}[{inner}]" if inner else origin.__name__
    if ann is type(None):
        return "None"
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        return f"{ann.__name__} ({', '.join(str(m.value) for m in ann)})"
    return getattr(ann, "__name__", str(ann))


def instrument_fields(cls: type[InstrumentBase]) -> list[dict[str, Any]]:
    """Name, type, requiredness, default and description of every defining field."""
    out = []
    for name, field in cls.model_fields.items():
        if name == "product_type":
            continue
        default: Any = None
        if not field.is_required():
            d = field.get_default(call_default_factory=True)
            default = d.value if isinstance(d, enum.Enum) else d
            if default is not None and not isinstance(default, (str, int, float, bool)):
                default = str(default)
        out.append(
            {
                "name": name,
                "type": _type_name(field.annotation),
                "required": field.is_required(),
                "default": default,
                "description": field.description or "",
            }
        )
    return out


def product_reference() -> dict[str, Any]:
    """Asset class -> products, each with venue, model, methodology and defining fields."""
    classes = instrument_classes()
    groups: list[dict[str, Any]] = []
    for ac in AssetClass:
        products = []
        for pt, spec in PRODUCT_CATALOGUE.items():
            if PRODUCT_ASSET_CLASS[pt] is not ac:
                continue
            cls = classes.get(pt)
            products.append(
                {
                    "product_type": pt.value,
                    "name": spec.name,
                    "asset_class": ac.value,
                    "venue": VENUE_BY_PRODUCT[pt].value,
                    "model": spec.model,
                    "model_label": spec.model_label,
                    "model_version": spec.model_version,
                    "methodology": spec.methodology,
                    "methodology_title": spec.methodology_title,
                    "instrument_class": cls.__name__ if cls else None,
                    "fields": instrument_fields(cls) if cls else [],
                }
            )
        groups.append({"asset_class": ac.value, "name": ASSET_CLASS_NAMES[ac], "products": products})
    return {"asset_classes": groups}
