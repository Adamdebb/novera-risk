"""Base market levels shared by the trade generator and the market-data simulator.

Keeping them in one place makes trade prices, curves and spots mutually consistent.
Levels are plausible for the business date (September 2026), not real quotes.
"""

from __future__ import annotations

# Par swap / government curve levels by currency and tenor (decimal rates).
CURVE_TENORS: tuple[str, ...] = ("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
TENOR_YEARS: dict[str, float] = {
    "1M": 1 / 12,
    "3M": 0.25,
    "6M": 0.5,
    "1Y": 1,
    "2Y": 2,
    "3Y": 3,
    "5Y": 5,
    "7Y": 7,
    "10Y": 10,
    "15Y": 15,
    "20Y": 20,
    "30Y": 30,
}

SWAP_CURVES: dict[str, dict[str, float]] = {
    "USD": {
        "1M": 0.0395,
        "3M": 0.0392,
        "6M": 0.0388,
        "1Y": 0.0380,
        "2Y": 0.0372,
        "3Y": 0.0370,
        "5Y": 0.0374,
        "7Y": 0.0380,
        "10Y": 0.0390,
        "15Y": 0.0402,
        "20Y": 0.0408,
        "30Y": 0.0405,
    },
    "EUR": {
        "1M": 0.0215,
        "3M": 0.0212,
        "6M": 0.0210,
        "1Y": 0.0205,
        "2Y": 0.0208,
        "3Y": 0.0215,
        "5Y": 0.0228,
        "7Y": 0.0240,
        "10Y": 0.0255,
        "15Y": 0.0270,
        "20Y": 0.0272,
        "30Y": 0.0262,
    },
    "GBP": {
        "1M": 0.0410,
        "3M": 0.0405,
        "6M": 0.0398,
        "1Y": 0.0385,
        "2Y": 0.0378,
        "3Y": 0.0380,
        "5Y": 0.0390,
        "7Y": 0.0400,
        "10Y": 0.0415,
        "15Y": 0.0430,
        "20Y": 0.0435,
        "30Y": 0.0425,
    },
    "JPY": {
        "1M": 0.0050,
        "3M": 0.0055,
        "6M": 0.0060,
        "1Y": 0.0070,
        "2Y": 0.0085,
        "3Y": 0.0095,
        "5Y": 0.0110,
        "7Y": 0.0125,
        "10Y": 0.0150,
        "15Y": 0.0180,
        "20Y": 0.0200,
        "30Y": 0.0215,
    },
    "MXN": {
        "1M": 0.0850,
        "3M": 0.0840,
        "6M": 0.0830,
        "1Y": 0.0815,
        "2Y": 0.0800,
        "3Y": 0.0795,
        "5Y": 0.0800,
        "7Y": 0.0810,
        "10Y": 0.0825,
        "15Y": 0.0840,
        "20Y": 0.0845,
        "30Y": 0.0845,
    },
    "BRL": {
        "1M": 0.1200,
        "3M": 0.1190,
        "6M": 0.1180,
        "1Y": 0.1160,
        "2Y": 0.1150,
        "3Y": 0.1150,
        "5Y": 0.1160,
        "7Y": 0.1170,
        "10Y": 0.1180,
        "15Y": 0.1185,
        "20Y": 0.1185,
        "30Y": 0.1185,
    },
    "ZAR": {
        "1M": 0.0780,
        "3M": 0.0775,
        "6M": 0.0770,
        "1Y": 0.0765,
        "2Y": 0.0770,
        "3Y": 0.0780,
        "5Y": 0.0810,
        "7Y": 0.0840,
        "10Y": 0.0880,
        "15Y": 0.0920,
        "20Y": 0.0940,
        "30Y": 0.0950,
    },
    # Currencies traded only through FX; curves needed for forward pricing.
    "CHF": {
        "1M": 0.0045,
        "3M": 0.0045,
        "6M": 0.0048,
        "1Y": 0.0052,
        "2Y": 0.0060,
        "3Y": 0.0068,
        "5Y": 0.0080,
        "7Y": 0.0090,
        "10Y": 0.0102,
        "15Y": 0.0115,
        "20Y": 0.0120,
        "30Y": 0.0120,
    },
    "CAD": {
        "1M": 0.0300,
        "3M": 0.0298,
        "6M": 0.0292,
        "1Y": 0.0285,
        "2Y": 0.0282,
        "3Y": 0.0285,
        "5Y": 0.0295,
        "7Y": 0.0305,
        "10Y": 0.0320,
        "15Y": 0.0335,
        "20Y": 0.0340,
        "30Y": 0.0338,
    },
    "AUD": {
        "1M": 0.0400,
        "3M": 0.0398,
        "6M": 0.0392,
        "1Y": 0.0385,
        "2Y": 0.0380,
        "3Y": 0.0382,
        "5Y": 0.0392,
        "7Y": 0.0402,
        "10Y": 0.0418,
        "15Y": 0.0435,
        "20Y": 0.0442,
        "30Y": 0.0440,
    },
    "NZD": {
        "1M": 0.0420,
        "3M": 0.0415,
        "6M": 0.0405,
        "1Y": 0.0392,
        "2Y": 0.0385,
        "3Y": 0.0388,
        "5Y": 0.0398,
        "7Y": 0.0408,
        "10Y": 0.0425,
        "15Y": 0.0440,
        "20Y": 0.0445,
        "30Y": 0.0442,
    },
    "SGD": {
        "1M": 0.0280,
        "3M": 0.0278,
        "6M": 0.0272,
        "1Y": 0.0265,
        "2Y": 0.0260,
        "3Y": 0.0262,
        "5Y": 0.0270,
        "7Y": 0.0278,
        "10Y": 0.0290,
        "15Y": 0.0300,
        "20Y": 0.0305,
        "30Y": 0.0305,
    },
    "INR": {
        "1M": 0.0650,
        "3M": 0.0650,
        "6M": 0.0655,
        "1Y": 0.0660,
        "2Y": 0.0665,
        "3Y": 0.0670,
        "5Y": 0.0680,
        "7Y": 0.0690,
        "10Y": 0.0700,
        "15Y": 0.0710,
        "20Y": 0.0715,
        "30Y": 0.0715,
    },
    "TRY": {
        "1M": 0.4200,
        "3M": 0.4100,
        "6M": 0.3900,
        "1Y": 0.3600,
        "2Y": 0.3200,
        "3Y": 0.3000,
        "5Y": 0.2800,
        "7Y": 0.2700,
        "10Y": 0.2600,
        "15Y": 0.2500,
        "20Y": 0.2500,
        "30Y": 0.2500,
    },
    "ARS": {
        "1M": 0.5500,
        "3M": 0.5300,
        "6M": 0.5000,
        "1Y": 0.4600,
        "2Y": 0.4200,
        "3Y": 0.4000,
        "5Y": 0.3800,
        "7Y": 0.3700,
        "10Y": 0.3600,
        "15Y": 0.3500,
        "20Y": 0.3500,
        "30Y": 0.3500,
    },
}
# Currencies with a government bond market in the simulated bank.
BOND_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "MXN", "BRL", "ZAR")

# Daily zero-rate volatility (decimal) per currency, and equity/vol correlation inputs.
RATES_DAILY_VOL: dict[str, float] = {
    "USD": 0.00060,
    "EUR": 0.00045,
    "GBP": 0.00060,
    "JPY": 0.00025,
    "MXN": 0.00100,
    "BRL": 0.00130,
    "ZAR": 0.00110,
    "CHF": 0.00035,
    "CAD": 0.00050,
    "AUD": 0.00055,
    "NZD": 0.00055,
    "SGD": 0.00040,
    "INR": 0.00040,
    "TRY": 0.00400,
    "ARS": 0.00600,
}

# Vol surface grid and smile parameters.
VOL_EXPIRIES: tuple[str, ...] = ("1M", "3M", "6M", "1Y", "2Y")
VOL_MONEYNESS: tuple[float, ...] = (0.80, 0.90, 1.00, 1.10, 1.20)
# Commodity curve tenors and annualised slope (positive = contango) per commodity.
COMMODITY_TENORS: tuple[str, ...] = ("1M", "3M", "6M", "1Y", "2Y", "3Y")
COMMODITY_SLOPE: dict[str, float] = {
    "BRENT": -0.04,
    "WTI": -0.05,
    "NATGAS": 0.10,
    "GOLD": 0.045,
    "SILVER": 0.045,
    "COPPER": 0.01,
    "ALUMINIUM": 0.02,
}

# Government spread to swap curve (decimal). Negative means govies yield less than swaps.
GOVT_SPREAD: dict[str, float] = {
    "USD": 0.0005,
    "EUR": -0.0035,
    "GBP": 0.0005,
    "JPY": -0.0005,
    "MXN": 0.0020,
    "BRL": 0.0050,
    "ZAR": 0.0080,
}
GOVT_ISSUER: dict[str, str] = {
    "USD": "US Treasury",
    "EUR": "Bundesrepublik Deutschland",
    "GBP": "HM Treasury",
    "JPY": "Japan MoF",
    "MXN": "Estados Unidos Mexicanos",
    "BRL": "Tesouro Nacional",
    "ZAR": "Republic of South Africa",
}
FLOAT_INDEX: dict[str, str] = {
    "USD": "USD-SOFR",
    "EUR": "EUR-EURIBOR-6M",
    "GBP": "GBP-SONIA",
    "JPY": "JPY-TONA",
    "MXN": "MXN-TIIE-28D",
    "BRL": "BRL-CDI",
    "ZAR": "ZAR-JIBAR-3M",
}

# FX spot, quote per base. Pair -> (spot, annualised vol)
FX_SPOT: dict[str, tuple[float, float]] = {
    "EUR/USD": (1.0850, 0.075),
    "GBP/USD": (1.2750, 0.085),
    "USD/JPY": (146.50, 0.10),
    "AUD/USD": (0.6650, 0.11),
    "USD/CHF": (0.8800, 0.08),
    "USD/CAD": (1.3600, 0.06),
    "NZD/USD": (0.6050, 0.12),
    "EUR/GBP": (0.8510, 0.06),
    "USD/MXN": (18.20, 0.13),
    "USD/BRL": (5.35, 0.17),
    "USD/ZAR": (17.80, 0.16),
    "USD/INR": (84.50, 0.05),
    "USD/TRY": (38.50, 0.25),
    "USD/ARS": (1250.0, 0.35),
    "USD/SGD": (1.3400, 0.05),
}

# Equities: ticker -> (exchange, currency, sector, country, price, annual vol)
EQUITIES: dict[str, tuple[str, str, str, str, float, float]] = {
    "AAPL": ("NASDAQ", "USD", "Technology", "US", 228.0, 0.26),
    "MSFT": ("NASDAQ", "USD", "Technology", "US", 425.0, 0.24),
    "NVDA": ("NASDAQ", "USD", "Technology", "US", 132.0, 0.45),
    "JPM": ("NYSE", "USD", "Financials", "US", 215.0, 0.24),
    "XOM": ("NYSE", "USD", "Energy", "US", 118.0, 0.22),
    "PFE": ("NYSE", "USD", "Healthcare", "US", 29.0, 0.22),
    "TSLA": ("NASDAQ", "USD", "Consumer", "US", 245.0, 0.55),
    "BA": ("NYSE", "USD", "Industrials", "US", 178.0, 0.35),
    "SAP": ("XETRA", "EUR", "Technology", "DE", 214.0, 0.24),
    "ASML": ("EURONEXT", "EUR", "Technology", "NL", 690.0, 0.34),
    "SIE": ("XETRA", "EUR", "Industrials", "DE", 178.0, 0.24),
    "TTE": ("EURONEXT", "EUR", "Energy", "FR", 60.0, 0.22),
    "BNP": ("EURONEXT", "EUR", "Financials", "FR", 62.0, 0.28),
    "NESN": ("SIX", "CHF", "Consumer", "CH", 88.0, 0.17),
    "HSBA": ("LSE", "GBP", "Financials", "GB", 6.9, 0.22),
    "SHEL": ("LSE", "GBP", "Energy", "GB", 27.5, 0.23),
}

# Indices: code -> (exchange, currency, level, vol, future multiplier)
EQUITY_INDICES: dict[str, tuple[str, str, float, float, float]] = {
    "SPX": ("CME", "USD", 5600.0, 0.16, 50.0),
    "NDX": ("CME", "USD", 19500.0, 0.21, 20.0),
    "SX5E": ("EUREX", "EUR", 4950.0, 0.17, 10.0),
    "DAX": ("EUREX", "EUR", 18600.0, 0.17, 25.0),
    "FTSE": ("ICE", "GBP", 8250.0, 0.13, 10.0),
    "NKY": ("OSE", "JPY", 38500.0, 0.20, 1000.0),
}

# Commodities: code -> (exchange, currency, price, vol, contract size, unit)
COMMODITIES: dict[str, tuple[str, str, float, float, float, str]] = {
    "BRENT": ("ICE", "USD", 74.0, 0.32, 1000.0, "bbl"),
    "WTI": ("CME", "USD", 70.5, 0.34, 1000.0, "bbl"),
    "NATGAS": ("CME", "USD", 2.85, 0.60, 10000.0, "MMBtu"),
    "GOLD": ("CME", "USD", 2650.0, 0.15, 100.0, "oz"),
    "SILVER": ("CME", "USD", 31.0, 0.28, 5000.0, "oz"),
    "COPPER": ("CME", "USD", 4.25, 0.24, 25000.0, "lb"),
    "ALUMINIUM": ("ICE", "USD", 2450.0, 0.22, 25.0, "t"),
}

# CDS indices: family -> (currency, series, coupon, spread bp, recovery)
CDS_INDICES: dict[str, tuple[str, int, float, float, float]] = {
    "CDX.NA.IG": ("USD", 45, 0.01, 52.0, 0.40),
    "ITRAXX.EUR.MAIN": ("EUR", 44, 0.01, 58.0, 0.40),
    "CDX.NA.HY": ("USD", 45, 0.05, 340.0, 0.30),
    "ITRAXX.EUR.XOVER": ("EUR", 44, 0.05, 310.0, 0.30),
}

# Crypto: symbol -> (venue, price, vol)
CRYPTO: dict[str, tuple[str, float, float]] = {
    "BTC": ("COINBASE", 62000.0, 0.60),
    "ETH": ("COINBASE", 2450.0, 0.70),
}
