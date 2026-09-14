"""Fund-face reference assumptions: prime-broker margin schedules, crowding scores, factor set."""

from __future__ import annotations

# Margin rate on gross market value or notional per product, per prime broker. Synthetic.
PB_MARGIN: dict[str, dict[str, float]] = {
    "PB_GS": {
        "CASH_EQUITY": 0.20,
        "EQUITY_OPTION": 0.25,
        "EQUITY_INDEX_FUTURE": 0.06,
        "FX_SPOT": 0.02,
        "FX_FORWARD": 0.03,
        "FX_OPTION": 0.06,
        "INTEREST_RATE_SWAP": 0.012,
        "GOVERNMENT_BOND": 0.03,
        "CDS_INDEX": 0.05,
        "COMMODITY_FUTURE": 0.10,
        "CRYPTO_SPOT": 0.35,
    },
    "PB_MS": {
        "CASH_EQUITY": 0.22,
        "EQUITY_OPTION": 0.28,
        "EQUITY_INDEX_FUTURE": 0.07,
        "FX_SPOT": 0.02,
        "FX_FORWARD": 0.035,
        "FX_OPTION": 0.07,
        "INTEREST_RATE_SWAP": 0.014,
        "GOVERNMENT_BOND": 0.035,
        "CDS_INDEX": 0.06,
        "COMMODITY_FUTURE": 0.11,
        "CRYPTO_SPOT": 0.40,
    },
    "PB_JPM": {
        "CASH_EQUITY": 0.18,
        "EQUITY_OPTION": 0.24,
        "EQUITY_INDEX_FUTURE": 0.06,
        "FX_SPOT": 0.015,
        "FX_FORWARD": 0.03,
        "FX_OPTION": 0.06,
        "INTEREST_RATE_SWAP": 0.011,
        "GOVERNMENT_BOND": 0.03,
        "CDS_INDEX": 0.05,
        "COMMODITY_FUTURE": 0.09,
        "CRYPTO_SPOT": 0.50,
    },
    "PB_BARC": {
        "CASH_EQUITY": 0.25,
        "EQUITY_OPTION": 0.30,
        "EQUITY_INDEX_FUTURE": 0.08,
        "FX_SPOT": 0.02,
        "FX_FORWARD": 0.04,
        "FX_OPTION": 0.08,
        "INTEREST_RATE_SWAP": 0.015,
        "GOVERNMENT_BOND": 0.04,
        "CDS_INDEX": 0.07,
        "COMMODITY_FUTURE": 0.12,
        "CRYPTO_SPOT": 0.60,
    },
}
# Exchange-traded positions are held at the fund's equity prime brokers by book (simplified routing).
LISTED_PB: dict[str, str] = {
    "EXCH_NYSE": "PB_GS",
    "EXCH_XETRA": "PB_BARC",
    "EXCH_EUREX": "PB_BARC",
    "EXCH_CME": "PB_GS",
    "EXCH_ICE": "PB_MS",
    "EXCH_COINBASE": "PB_GS",
    "EXCH_DTC": "PB_JPM",
    "CCP_LCH": "PB_JPM",
    "CCP_CME": "PB_JPM",
    "CCP_ICE": "PB_MS",
}
NETTING_BENEFIT = 0.25  # share of offsetting long/short margin within an asset class and broker
CROWDING_SURCHARGE = 0.10  # extra margin rate on crowded names (score above 0.7)
LIQUIDITY_BUFFER = 0.15  # unencumbered cash the fund keeps, share of NAV

# Crowding score 0..1 from a synthetic peer-ownership universe.
CROWDING: dict[str, float] = {
    "NVDA": 0.95,
    "TSLA": 0.80,
    "AAPL": 0.65,
    "MSFT": 0.60,
    "ASML": 0.70,
    "SAP": 0.30,
    "JPM": 0.40,
    "XOM": 0.35,
    "PFE": 0.20,
    "BA": 0.45,
    "SIE": 0.25,
    "TTE": 0.30,
    "BNP": 0.35,
    "NESN": 0.20,
    "HSBA": 0.25,
    "SHEL": 0.30,
    "SPX": 0.20,
    "NDX": 0.35,
    "SX5E": 0.15,
    "DAX": 0.15,
    "FTSE": 0.10,
    "NKY": 0.20,
    "BTC": 0.75,
    "ETH": 0.60,
    "BRENT": 0.40,
    "WTI": 0.40,
    "NATGAS": 0.30,
    "GOLD": 0.55,
    "SILVER": 0.35,
    "COPPER": 0.45,
    "ALUMINIUM": 0.20,
}

# Factor set for the regression: factor id -> (label, shock type handled by scenarios).
FACTORS: dict[str, str] = {
    "EQIDX:SPX": "US equity",
    "IR:USD:10Y": "USD 10Y rate",
    "CDS:CDX.NA.HY": "HY credit spread",
    "FX:EURUSD": "EUR/USD",
    "CMD:BRENT:1M": "Brent",
    "CRYPTO:BTC": "Bitcoin",
    "VOL:SPX:3M:1.00": "SPX implied vol",
}
