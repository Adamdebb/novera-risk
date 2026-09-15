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


class Appropriateness(enum.StrEnum):
    """How close the model used is to what a trading desk would accept for the product.

    The rating is a methodology judgment recorded in MV-001, not a validation opinion:
    it says how far the implemented model is from the market standard, and the
    ``simplifications`` text says exactly where.
    """

    MARKET_STANDARD = "market_standard"
    """The model is what the market uses for this product; residual differences are conventions."""
    ACCEPTABLE_SIMPLIFICATION = "acceptable_simplification"
    """A documented simplification whose error is small for the book as simulated."""
    KNOWN_WEAKNESS = "known_weakness"
    """A simplification that can misstate value or risk for parts of the book; upgrade planned."""


APPROPRIATENESS_LABELS: dict[Appropriateness, str] = {
    Appropriateness.MARKET_STANDARD: "Market standard",
    Appropriateness.ACCEPTABLE_SIMPLIFICATION: "Acceptable simplification",
    Appropriateness.KNOWN_WEAKNESS: "Known weakness",
}

MODEL_INVENTORY_RECORD = "MV-001"
MODEL_INVENTORY_VERSION = "1.0.0"


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
    market_standard: str
    """The model a trading desk or a validation team would expect for this product."""
    simplifications: str
    """Where the model used departs from the market standard, and why it is tolerated."""
    appropriateness: Appropriateness
    validation: str
    """How the implementation is checked: external benchmark or analytic identity, and where."""


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
            market_standard="Discounting off a bootstrapped government curve per issuer, ACT/ACT "
            "day count, holiday calendars.",
            simplifications="Government-to-swap spread is one constant per currency rather than "
            "a curve; ACT/ACT approximated as ACT/365.25; no holiday calendars; no "
            "inflation-linked or callable bonds.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="QuantLib benchmark: ql.FixedRateBond dirty price within 0.02% "
            "(tests/test_pricing.py::test_bond_matches_quantlib).",
        ),
        ProductSpec(
            ProductType.INTEREST_RATE_SWAP,
            "Interest-rate swap",
            "swap_single_curve",
            "Single-curve discounting and forwarding",
            SWAP_MODEL_VERSION,
            "PR-002",
            "Interest-rate swap valuation",
            market_standard="Multi-curve: OIS discounting (SOFR, ESTR, SONIA) with forwards "
            "projected off the index's own curve; CSA discounting for bilateral trades.",
            simplifications="One curve per currency for discounting and projection. Exact "
            "for the USD SOFR and GBP SONIA swaps in the simulated book, where the index is "
            "the OIS rate. EUR swaps reference EURIBOR-6M and ignore the ESTR/EURIBOR basis; "
            "the error is measured, not assumed (see validation). No stored fixings, no CSA "
            "discounting, no amortisation.",
            appropriateness=Appropriateness.KNOWN_WEAKNESS,
            validation="QuantLib benchmark: ql.VanillaSwap NPV within 2,000 on 100m and fair "
            "rate within 0.2bp (test_par_swap_has_zero_pv_and_matches_quantlib). Measured gap: "
            "single-curve PV of an in-the-money EUR swap against QuantLib with ESTR "
            "discounting and EURIBOR projection, bounded at 1% of PV "
            "(test_eur_swap_single_curve_gap_is_measured).",
        ),
        ProductSpec(
            ProductType.REPO,
            "Repo and reverse repo",
            "repo_cash_leg",
            "Cash leg off the zero curve",
            REPO_MODEL_VERSION,
            "PR-010",
            "Repo and reverse repo valuation",
            market_standard="Cash leg discounted off a repo curve (general collateral, with "
            "specials); collateral repriced with its haircut.",
            simplifications="Cash leg off the currency zero curve, no GC/special spread. "
            "Collateral is not repriced inside the market-risk PV; its requirement is "
            "recorded for the counterparty and liquidity views.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identities: zero PV at the curve-implied fair rate, DV01 sign, "
            "collateral requirement N/(1-h) (test_repo_zero_at_fair_rate_and_dv01_sign).",
        ),
        ProductSpec(
            ProductType.INTEREST_RATE_FUTURE,
            "Interest-rate future",
            "ir_future_forward",
            "Curve forward, no convexity adjustment",
            IR_FUTURE_MODEL_VERSION,
            "PR-011",
            "Interest-rate future valuation",
            market_standard="Curve forward plus a futures-to-forward convexity adjustment "
            "(Hull-White or the exchange convention), IMM calendar.",
            simplifications="No convexity adjustment (a few basis points at long expiries); "
            "IMM dates approximated as the third Friday without a holiday calendar.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identities: price reproduces the curve forward, tick-value "
            "identity, sign under a rate rise (test_ir_future_price_from_curve).",
        ),
        ProductSpec(
            ProductType.SWAPTION,
            "European swaption",
            "swaption_bachelier",
            "Bachelier on a normal volatility cube",
            SWAPTION_MODEL_VERSION,
            "PR-012",
            "European swaption valuation",
            market_standard="Bachelier (normal) on a SABR-calibrated smile cube, OIS "
            "discounting, cash-settlement annuity convention where applicable.",
            simplifications="The cube is quoted at the money and used for every strike, so "
            "out-of-the-money swaptions carry no smile; single-curve forwards; cash and "
            "physical settlement priced alike.",
            appropriateness=Appropriateness.KNOWN_WEAKNESS,
            validation="QuantLib benchmark: ql.BachelierSwaptionEngine within 0.2% and "
            "ql.bachelierBlackFormula (test_swaption_matches_quantlib_bachelier, "
            "test_bachelier_matches_quantlib); payer minus receiver equals A(S-K).",
        ),
        ProductSpec(
            ProductType.FX_SPOT,
            "FX spot",
            "fx_spot_mtm",
            "Mark to market at spot",
            FX_FORWARD_MODEL_VERSION,
            "PR-003",
            "FX spot and forward valuation",
            market_standard="Mark to market at spot.",
            simplifications="Unsettled spot is not discounted to its T+2 settlement.",
            appropriateness=Appropriateness.MARKET_STANDARD,
            validation="Analytic identities: spot mark, settled trade returns zero "
            "(test_fx_forward_cip_and_spot).",
        ),
        ProductSpec(
            ProductType.FX_FORWARD,
            "FX forward",
            "fx_forward_cip",
            "Covered interest parity",
            FX_FORWARD_MODEL_VERSION,
            "PR-003",
            "FX spot and forward valuation",
            market_standard="Forward from spot and quoted forward points, which embed the "
            "cross-currency basis; discounted at the collateral currency curve.",
            simplifications="Covered interest parity on the two zero curves; no cross-currency "
            "basis, so forwards sit on the interest differential alone.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identities: CIP reproduced, sign of the forward points matches "
            "the rate differential (test_fx_forward_cip_and_spot).",
        ),
        ProductSpec(
            ProductType.FX_OPTION,
            "FX vanilla option",
            "fx_option_garman_kohlhagen",
            "Garman-Kohlhagen",
            FX_OPTION_MODEL_VERSION,
            "PR-004",
            "FX vanilla option valuation",
            market_standard="Garman-Kohlhagen on a delta-quoted smile (at-the-money, risk "
            "reversal, butterfly) with premium-currency delta conventions.",
            simplifications="Smile read at moneyness K/F rather than at delta; no "
            "premium-adjusted delta convention; forward from CIP without cross-currency basis.",
            appropriateness=Appropriateness.MARKET_STANDARD,
            validation="QuantLib benchmark: ql.blackFormula to 1e-10; put-call parity "
            "(test_fx_option_matches_quantlib_black_and_parity).",
        ),
        ProductSpec(
            ProductType.CASH_EQUITY,
            "Cash equity",
            "equity_mtm",
            "Mark to market",
            EQUITY_MODEL_VERSION,
            "PR-005",
            "Cash equity and index future valuation",
            market_standard="Mark to market at the close.",
            simplifications="None.",
            appropriateness=Appropriateness.MARKET_STANDARD,
            validation="Analytic identity: shares times spot (test_equity_cash_future_option).",
        ),
        ProductSpec(
            ProductType.EQUITY_INDEX_FUTURE,
            "Equity index future",
            "index_future_carry",
            "Cost of carry",
            EQUITY_MODEL_VERSION,
            "PR-005",
            "Cash equity and index future valuation",
            market_standard="Mark to the listed contract price, or cost of carry with "
            "financing and the index dividend yield.",
            simplifications="Carry at the zero rate with no dividend yield and no futures "
            "basis; the fair value overstates the forward by the dividend yield.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identity: (F - K) with F = S/DF(T) (test_equity_cash_future_option).",
        ),
        ProductSpec(
            ProductType.EQUITY_OPTION,
            "Equity vanilla option",
            "equity_option_black_scholes",
            "Black-Scholes",
            EQUITY_OPTION_MODEL_VERSION,
            "PR-006",
            "Equity vanilla option valuation",
            market_standard="Black-Scholes on the dividend-adjusted forward with the smile at "
            "K/F; American exercise for listed single names (binomial tree or "
            "Bjerksund-Stensland).",
            simplifications="No dividends; European exercise for listed options that are "
            "American (small for the short-dated, out-of-the-money book simulated).",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="QuantLib benchmark: ql.blackFormula to 1e-10; put-call parity "
            "(test_equity_cash_future_option).",
        ),
        ProductSpec(
            ProductType.ETF,
            "ETF",
            "fund_lookthrough",
            "NAV by look-through to constituents",
            FUND_LOOKTHROUGH_MODEL_VERSION,
            "PR-015",
            "ETF and mutual-fund valuation by look-through",
            market_standard="Mark to the listed ETF price; look-through to constituents for "
            "risk and concentration.",
            simplifications="Look-through NAV with a constant tracking spread stands in for the "
            "listed price; static basket, no rebalancing or corporate actions.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identities: NAV from constituents, tracking spread applied, "
            "constituent factors in the dependency map (test_fund_lookthrough_pricing).",
        ),
        ProductSpec(
            ProductType.MUTUAL_FUND,
            "Mutual fund",
            "fund_lookthrough",
            "NAV by look-through to constituents",
            FUND_LOOKTHROUGH_MODEL_VERSION,
            "PR-015",
            "ETF and mutual-fund valuation by look-through",
            market_standard="Latest published NAV; look-through to constituents for risk.",
            simplifications="NAV recomputed continuously from a static basket although the "
            "fund deals once a day with notice; cash sleeve as a constant.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Analytic identities: NAV from constituents plus cash sleeve "
            "(test_fund_lookthrough_pricing).",
        ),
        ProductSpec(
            ProductType.EQUITY_EXOTIC,
            "Equity barrier and digital option",
            "exotic_closed_form_bs",
            "Reiner-Rubinstein barriers, cash-or-nothing digitals",
            EXOTIC_MODEL_VERSION,
            "PR-016",
            "Equity barrier and digital option valuation",
            market_standard="Local or stochastic volatility with discrete barrier monitoring, "
            "priced by PDE or Monte Carlo; digitals as tight call spreads on the smile.",
            simplifications="Closed-form Black-Scholes with one vol per option and continuous "
            "monitoring; barrier risk near the barrier is understated; no dividends. Carries "
            "the FRTB residual risk add-on for that reason.",
            appropriateness=Appropriateness.KNOWN_WEAKNESS,
            validation="QuantLib benchmark: ql.AnalyticBarrierEngine to 1e-8 on sixteen "
            "barrier cases with and without rebate; digitals against "
            "ql.AnalyticEuropeanEngine (test_barrier_and_digital_formulas_match_quantlib).",
        ),
        ProductSpec(
            ProductType.CDS_INDEX,
            "CDS index",
            "cds_flat_hazard",
            "Flat hazard rate (ISDA standard model, simplified)",
            CDS_MODEL_VERSION,
            "PR-008",
            "CDS index valuation",
            market_standard="ISDA standard model: hazard curve bootstrapped from the quoted "
            "term structure, IMM dates, upfront and running-coupon conventions.",
            simplifications="Flat hazard from the credit triangle; quarterly periods from the "
            "first of the month rather than IMM dates; no upfront. Suitable for risk, not "
            "settlement.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="QuantLib benchmark: ql.MidPointCdsEngine on a flat hazard within 5% "
            "(test_cds_index_matches_quantlib_within_tolerance); PV rises with spread for a "
            "protection buyer.",
        ),
        ProductSpec(
            ProductType.CDS_SINGLE_NAME,
            "Single-name CDS",
            "cds_flat_hazard",
            "Flat hazard rate (shared with the index model)",
            CDS_MODEL_VERSION,
            "PR-013",
            "Single-name CDS valuation",
            market_standard="ISDA standard model with a bootstrapped hazard curve per entity "
            "and the entity's recovery assumption.",
            simplifications="Same flat-hazard model as the index; one par spread per entity, "
            "no term structure of hazard; no upfront.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="Shares the benchmarked index legs; identities on model id, spread "
            "pass-through and sign (test_single_name_cds_and_index_share_the_model).",
        ),
        ProductSpec(
            ProductType.COMMODITY_FUTURE,
            "Commodity future",
            "commodity_future_curve",
            "Mark off the futures curve",
            COMMODITY_MODEL_VERSION,
            "PR-007",
            "Commodity future valuation",
            market_standard="Mark to the listed contract price.",
            simplifications="Linear interpolation on the tenor curve between listed contracts "
            "when the expiry sits between curve points.",
            appropriateness=Appropriateness.MARKET_STANDARD,
            validation="Analytic identity: contracts times size times (F - K) off the curve "
            "(test_commodity_future_off_curve).",
        ),
        ProductSpec(
            ProductType.COMMODITY_OPTION,
            "Commodity option",
            "commodity_option_black76",
            "Black 76 on the curve price",
            COMMODITY_OPTION_MODEL_VERSION,
            "PR-014",
            "Commodity option valuation",
            market_standard="Black 76 on the futures price with the contract's own smile; "
            "American exercise for options on futures.",
            simplifications="European exercise; the surface is quoted on the equity moneyness "
            "grid rather than per contract.",
            appropriateness=Appropriateness.ACCEPTABLE_SIMPLIFICATION,
            validation="QuantLib benchmark: ql.blackFormula; put-call parity C - P = DF(F - K) "
            "(test_commodity_option_black76_and_parity).",
        ),
        ProductSpec(
            ProductType.CRYPTO_SPOT,
            "Crypto spot (BTC, ETH)",
            "crypto_mtm",
            "Mark to market",
            CRYPTO_MODEL_VERSION,
            "PR-009",
            "Digital-asset spot valuation",
            market_standard="Mark to market at the venue or composite price.",
            simplifications="One USD price per asset; no venue basis.",
            appropriateness=Appropriateness.MARKET_STANDARD,
            validation="Analytic identity: units times spot (test_crypto_spot).",
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
                    "market_standard": spec.market_standard,
                    "simplifications": spec.simplifications,
                    "appropriateness": spec.appropriateness.value,
                    "appropriateness_label": APPROPRIATENESS_LABELS[spec.appropriateness],
                    "validation": spec.validation,
                    "instrument_class": cls.__name__ if cls else None,
                    "fields": instrument_fields(cls) if cls else [],
                }
            )
        groups.append({"asset_class": ac.value, "name": ASSET_CLASS_NAMES[ac], "products": products})
    return {"asset_classes": groups}


def model_inventory() -> dict[str, Any]:
    """Every product with the model used, the market standard, the simplifications and the
    appropriateness rating (MV-001). Flat rows, one per product, in asset-class order."""
    rows = []
    for ac in AssetClass:
        for pt, spec in PRODUCT_CATALOGUE.items():
            if PRODUCT_ASSET_CLASS[pt] is not ac:
                continue
            rows.append(
                {
                    "product_type": pt.value,
                    "name": spec.name,
                    "asset_class": ac.value,
                    "asset_class_name": ASSET_CLASS_NAMES[ac],
                    "model": spec.model,
                    "model_label": spec.model_label,
                    "model_version": spec.model_version,
                    "methodology": spec.methodology,
                    "market_standard": spec.market_standard,
                    "simplifications": spec.simplifications,
                    "appropriateness": spec.appropriateness.value,
                    "appropriateness_label": APPROPRIATENESS_LABELS[spec.appropriateness],
                    "validation": spec.validation,
                }
            )
    summary = {a.value: sum(r["appropriateness"] == a.value for r in rows) for a in Appropriateness}
    return {
        "record": MODEL_INVENTORY_RECORD,
        "version": MODEL_INVENTORY_VERSION,
        "rows": rows,
        "summary": summary,
    }


def render_model_inventory() -> str:
    """The inventory as the markdown table kept in ``docs/methodology/MV-001``; the test suite
    checks the committed table equals this rendering so the record cannot drift from the code."""
    inv = model_inventory()
    lines = [
        "| Product | Model used | Market standard | Simplifications | Rating | Validation |",
        "|---|---|---|---|---|---|",
    ]
    for r in inv["rows"]:
        lines.append(
            f"| **{r['name']}** ({r['asset_class_name']}) "
            f"| {r['model_label']} · `{r['model']}` v{r['model_version']} · {r['methodology']} "
            f"| {r['market_standard']} | {r['simplifications']} "
            f"| {r['appropriateness_label']} | {r['validation']} |"
        )
    s = inv["summary"]
    lines.append("")
    lines.append(
        f"{len(inv['rows'])} products: "
        + ", ".join(f"{s[a.value]} {APPROPRIATENESS_LABELS[a].lower()}" for a in Appropriateness)
        + "."
    )
    return "\n".join(lines) + "\n"
