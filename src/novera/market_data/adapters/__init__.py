"""Real market-data adapters. Optional: the platform runs on synthetic data by default.

Each adapter maps external series to Novera factor ids and returns the long format
(as_of, factor_id, value). ``fetch_all`` merges the sources; ``apply_real_history`` writes
them into the history table and records provenance so synthetic and real can be told apart.
"""

from novera.market_data.adapters.base import Adapter, FetchResult, apply_real_history, fetch_all
from novera.market_data.adapters.coinbase import CoinbaseAdapter
from novera.market_data.adapters.fred import FredAdapter
from novera.market_data.adapters.yahoo import YahooAdapter

__all__ = [
    "Adapter",
    "CoinbaseAdapter",
    "FetchResult",
    "FredAdapter",
    "YahooAdapter",
    "apply_real_history",
    "fetch_all",
]
