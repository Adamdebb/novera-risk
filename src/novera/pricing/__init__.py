"""One pricer per product. Pricers return present value and cashflows only.

``PRICERS`` is the registry used by valuation and by the risk engine.
"""

from novera.domain.enums import ProductType
from novera.pricing.base import Cashflow, Pricer, PricingError, PricingResult
from novera.pricing.breadth import (
    price_cds_single_name,
    price_commodity_option,
    price_equity_exotic,
    price_etf,
    price_interest_rate_future,
    price_mutual_fund,
    price_repo,
    price_swaption,
)
from novera.pricing.commodity import price_commodity_future
from novera.pricing.credit import price_cds_index
from novera.pricing.crypto import price_crypto_spot
from novera.pricing.equity import price_cash_equity, price_equity_index_future, price_equity_option
from novera.pricing.fx import price_fx_forward, price_fx_option, price_fx_spot
from novera.pricing.rates import price_government_bond, price_interest_rate_swap

PRICERS: dict[ProductType, Pricer] = {
    ProductType.GOVERNMENT_BOND: price_government_bond,
    ProductType.INTEREST_RATE_SWAP: price_interest_rate_swap,
    ProductType.FX_SPOT: price_fx_spot,
    ProductType.FX_FORWARD: price_fx_forward,
    ProductType.FX_OPTION: price_fx_option,
    ProductType.CASH_EQUITY: price_cash_equity,
    ProductType.EQUITY_INDEX_FUTURE: price_equity_index_future,
    ProductType.EQUITY_OPTION: price_equity_option,
    ProductType.COMMODITY_FUTURE: price_commodity_future,
    ProductType.CDS_INDEX: price_cds_index,
    ProductType.CRYPTO_SPOT: price_crypto_spot,
    ProductType.REPO: price_repo,
    ProductType.INTEREST_RATE_FUTURE: price_interest_rate_future,
    ProductType.SWAPTION: price_swaption,
    ProductType.CDS_SINGLE_NAME: price_cds_single_name,
    ProductType.COMMODITY_OPTION: price_commodity_option,
    ProductType.ETF: price_etf,
    ProductType.MUTUAL_FUND: price_mutual_fund,
    ProductType.EQUITY_EXOTIC: price_equity_exotic,
}

__all__ = ["PRICERS", "Cashflow", "Pricer", "PricingError", "PricingResult"]
