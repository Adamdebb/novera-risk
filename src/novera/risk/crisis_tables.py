"""Named real crises as calibrated replays (MR-018, decision 47.3).

Each table gives the approximate move of a few headline factors over the crisis window, in engine
units (decimals for rates, bp for CDS indices, fractions for prices, vol points for the S&P 500
3M at-the-money vol). A calibrated scenario applies these moves and fills every other factor
with its conditional expectation under stressed correlations, so a crisis the stored history
does not reach can still be run on today's book.

**The figures are drafts, not data.** They were written from widely published end-of-day levels
and are rounded; some (credit index spreads, implied vol at three months rather than VIX) are
estimates of the instrument the platform carries. They enter the library only as DRAFT proposals
from Risk Control, marked "approximate, to verify", and run only once the Head of Market Risk or
the CRO approves each (four eyes, OPS-005). Correct a figure by proposing a new version.
Factors that did not trade then (Bitcoin before 2010, for example) are left out and filled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

VERIFY_NOTE = (
    "Calibrated replay drafted from published market moves; figures are approximate and rounded. "
    "Verify each against your own data before approving."
)


@dataclass(frozen=True)
class CrisisTable:
    scenario_id: str
    name: str
    start: date
    end: date
    narrative: str
    moves: tuple[tuple[str, float], ...]
    """(factor id, move): rates as decimals, CDS in bp, prices as fractions, ``VOL:`` in vol points."""
    tags: tuple[str, ...] = ("HISTORICAL", "CROSS_ASSET")
    severity: str = "SEVERE"

    def rules(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fid, v in self.moves:
            if fid.startswith("VOL:"):
                out.append({"prefix": fid, "size": 0.0, "vol_points": v, "tenor_filter": None})
            else:
                out.append({"prefix": fid, "size": v, "vol_points": None, "tenor_filter": None})
        return out

    def definition(self, goal: str = "INFORMATION") -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "kind": "HYPOTHETICAL",
            "goal": goal,
            "description": (
                f"Calibrated replay of {self.start} to {self.end}; "
                "other factors move with stressed correlations"
            ),
            "rules": self.rules(),
            "expand": True,
            "covariance": "STRESSED",
            "horizon_days": 20,
            "tags": list(self.tags),
            "severity": self.severity,
            "narrative": self.narrative,
            "catalyst": "",
            "source": "CALIBRATED",
            "crisis_start": self.start.isoformat(),
            "crisis_end": self.end.isoformat(),
        }


SPX, SX5E, NKY, FTSE = "EQIDX:SPX", "EQIDX:SX5E", "EQIDX:NKY", "EQIDX:FTSE"
US2, US10, EU10 = "IR:USD:2Y", "IR:USD:10Y", "IR:EUR:10Y"
EURUSD, USDJPY, GBPUSD, USDCHF = "FX:EURUSD", "FX:USDJPY", "FX:GBPUSD", "FX:USDCHF"
IG, HY = "CDS:CDX.NA.IG", "CDS:CDX.NA.HY"
VOL = "VOL:SPX:3M:1.00"
BRENT, WTI, GOLD, BTC = "CMD:BRENT:1M", "CMD:WTI:1M", "CMD:GOLD:1M", "CRYPTO:BTC"

CRISIS_TABLES: tuple[CrisisTable, ...] = (
    CrisisTable(
        "gfc_2008",
        "2008 Global financial crisis",
        date(2008, 9, 12),
        date(2008, 10, 27),
        "Lehman fails, money markets freeze, forced deleveraging across every risky asset.",
        (
            (SPX, -0.32),
            (SX5E, -0.34),
            (US2, -0.0066),
            (US10, 0.0),
            (EU10, -0.0030),
            (EURUSD, -0.12),
            (USDJPY, -0.14),
            (IG, 80.0),
            (HY, 500.0),
            (VOL, 0.35),
            (BRENT, -0.38),
            (GOLD, -0.02),
        ),
        severity="EXTREME",
    ),
    CrisisTable(
        "euro_2011",
        "2011 Euro sovereign crisis",
        date(2011, 7, 22),
        date(2011, 9, 22),
        "Contagion to Italy and Spain, the US downgrade, a flight into Treasuries and Bunds.",
        (
            (SPX, -0.16),
            (SX5E, -0.27),
            (US2, -0.0020),
            (US10, -0.0125),
            (EU10, -0.0110),
            (EURUSD, -0.06),
            (USDJPY, -0.03),
            (IG, 50.0),
            (HY, 280.0),
            (VOL, 0.20),
            (BRENT, -0.11),
            (GOLD, 0.09),
        ),
    ),
    CrisisTable(
        "taper_2013",
        "2013 Taper tantrum",
        date(2013, 5, 21),
        date(2013, 6, 24),
        "The Fed signals the end of asset purchases; yields jump, emerging markets sell off.",
        (
            (SPX, -0.06),
            (SX5E, -0.10),
            (US2, 0.0017),
            (US10, 0.0062),
            (EU10, 0.0041),
            (EURUSD, 0.02),
            (USDJPY, -0.05),
            (IG, 21.0),
            (HY, 110.0),
            (VOL, 0.07),
            (BRENT, -0.03),
            (GOLD, -0.07),
            (BTC, -0.16),
        ),
        tags=("HISTORICAL", "RATES", "MACRO"),
        severity="MODERATE",
    ),
    CrisisTable(
        "chf_2015",
        "2015 Swiss franc de-peg",
        date(2015, 1, 14),
        date(2015, 1, 16),
        "The SNB abandons the EUR/CHF floor without warning; the franc jumps about 15% in minutes.",
        ((USDCHF, -0.15), (EURUSD, -0.02), ("IR:CHF:2Y", -0.0050), (SPX, -0.01), (GOLD, 0.04)),
        tags=("HISTORICAL", "JUMP", "FX"),
    ),
    CrisisTable(
        "china_2015",
        "2015 China devaluation shock",
        date(2015, 8, 10),
        date(2015, 8, 25),
        "A surprise yuan devaluation and a Chinese equity rout spill into global markets.",
        (
            (SPX, -0.11),
            (SX5E, -0.12),
            (US2, -0.0007),
            (US10, -0.0023),
            (EURUSD, 0.05),
            (USDJPY, -0.04),
            (IG, 13.0),
            (HY, 70.0),
            (VOL, 0.15),
            (BRENT, -0.14),
            (GOLD, 0.03),
            (BTC, -0.20),
        ),
    ),
    CrisisTable(
        "brexit_2016",
        "2016 Brexit vote",
        date(2016, 6, 23),
        date(2016, 6, 27),
        "The UK votes to leave the EU against the polls; sterling and European banks fall.",
        (
            (SPX, -0.053),
            (SX5E, -0.11),
            (FTSE, -0.06),
            (US2, -0.0017),
            (US10, -0.0028),
            (EU10, -0.0020),
            (GBPUSD, -0.11),
            (EURUSD, -0.03),
            (USDJPY, -0.04),
            (IG, 14.0),
            (HY, 60.0),
            (VOL, 0.08),
            (BRENT, -0.07),
            (GOLD, 0.05),
        ),
        tags=("HISTORICAL", "GEOPOLITICAL", "CROSS_ASSET"),
        severity="MODERATE",
    ),
    CrisisTable(
        "volmageddon_2018",
        "2018 Volmageddon",
        date(2018, 2, 1),
        date(2018, 2, 8),
        "Short-volatility products blow up; implied vol more than doubles in a day.",
        (
            (SPX, -0.085),
            (SX5E, -0.065),
            (US10, 0.0012),
            (EURUSD, -0.02),
            (IG, 10.0),
            (HY, 60.0),
            (VOL, 0.20),
            (BRENT, -0.06),
            (GOLD, -0.02),
            (BTC, -0.10),
        ),
        tags=("HISTORICAL", "VOLATILITY", "CROWDING"),
    ),
    CrisisTable(
        "covid_2020",
        "2020 COVID crash",
        date(2020, 2, 19),
        date(2020, 3, 23),
        "The pandemic shuts economies; a dash for cash hits even Treasuries, oil halves.",
        (
            (SPX, -0.34),
            (SX5E, -0.36),
            (US2, -0.0114),
            (US10, -0.0080),
            (EU10, 0.0008),
            (EURUSD, -0.01),
            (USDJPY, -0.01),
            (IG, 105.0),
            (HY, 550.0),
            (VOL, 0.40),
            (BRENT, -0.54),
            (GOLD, -0.02),
            (BTC, -0.36),
        ),
        tags=("HISTORICAL", "CROSS_ASSET", "LIQUIDITY"),
        severity="EXTREME",
    ),
    CrisisTable(
        "oil_negative_2020",
        "2020 Negative oil",
        date(2020, 4, 14),
        date(2020, 4, 21),
        "Storage runs out at Cushing; the expiring WTI contract settles below zero. Prices here are "
        "the next contract: relative shocks cannot cross zero.",
        ((WTI, -0.45), (BRENT, -0.35), (SPX, -0.04), (HY, 60.0), (VOL, 0.05)),
        tags=("HISTORICAL", "COMMODITY", "LIQUIDITY"),
    ),
    CrisisTable(
        "rates_2022",
        "2022 inflation and rates shock",
        date(2022, 1, 3),
        date(2022, 6, 14),
        "Inflation forces the fastest hiking cycle in decades; stocks and bonds fall together.",
        (
            (SPX, -0.22),
            (SX5E, -0.19),
            (US2, 0.0265),
            (US10, 0.0185),
            (EU10, 0.0187),
            (EURUSD, -0.08),
            (USDJPY, 0.17),
            (IG, 50.0),
            (HY, 270.0),
            (VOL, 0.15),
            (BRENT, 0.53),
            (GOLD, 0.01),
            (BTC, -0.52),
        ),
        tags=("HISTORICAL", "RATES", "MACRO"),
    ),
    CrisisTable(
        "ldi_2022",
        "2022 UK gilt and LDI crisis",
        date(2022, 9, 22),
        date(2022, 9, 28),
        "An unfunded budget meets leveraged pension hedges; gilts collapse until the BoE steps in.",
        (
            ("IR:GBP:2Y", 0.0080),
            ("IR:GBP:10Y", 0.0100),
            ("IR:GBP:30Y", 0.0125),
            (GBPUSD, -0.05),
            (FTSE, -0.02),
            (SPX, -0.014),
            (US10, 0.0010),
            (VOL, 0.03),
        ),
        tags=("HISTORICAL", "RATES", "LIQUIDITY"),
    ),
    CrisisTable(
        "banks_2023",
        "2023 regional banking stress",
        date(2023, 3, 8),
        date(2023, 3, 17),
        "SVB fails on rate losses and a deposit run; front-end yields collapse, Credit Suisse wobbles.",
        (
            (SPX, -0.02),
            (SX5E, -0.04),
            (US2, -0.0126),
            (US10, -0.0059),
            (EU10, -0.0057),
            (EURUSD, 0.01),
            (USDJPY, -0.04),
            (IG, 14.0),
            (HY, 70.0),
            (VOL, 0.07),
            (BRENT, -0.12),
            (GOLD, 0.096),
            (BTC, 0.26),
        ),
        tags=("HISTORICAL", "CREDIT", "RATES"),
        severity="MODERATE",
    ),
    CrisisTable(
        "yen_carry_2024",
        "2024 Yen carry unwind",
        date(2024, 7, 31),
        date(2024, 8, 5),
        "A Bank of Japan hike and weak US data force yen-funded positions out at once.",
        (
            (USDJPY, -0.04),
            (NKY, -0.20),
            (SPX, -0.06),
            (SX5E, -0.05),
            (US2, -0.0040),
            (US10, -0.0025),
            (EU10, -0.0013),
            (EURUSD, 0.01),
            (IG, 12.0),
            (HY, 60.0),
            (VOL, 0.15),
            (BRENT, -0.05),
            (GOLD, -0.015),
            (BTC, -0.16),
        ),
        tags=("HISTORICAL", "CROWDING", "FX"),
    ),
    CrisisTable(
        "tariffs_2025",
        "2025 Tariff shock",
        date(2025, 4, 2),
        date(2025, 4, 8),
        "Sweeping US tariffs are announced; equities fall, long Treasuries sell off with them.",
        (
            (SPX, -0.12),
            (SX5E, -0.10),
            (US2, -0.0015),
            (US10, 0.0016),
            (EU10, -0.0010),
            (EURUSD, 0.015),
            (USDJPY, -0.02),
            (IG, 25.0),
            (HY, 130.0),
            (VOL, 0.20),
            (BRENT, -0.16),
            (GOLD, -0.05),
            (BTC, -0.10),
        ),
        tags=("HISTORICAL", "GEOPOLITICAL", "MACRO"),
    ),
)

TABLE_BY_ID = {t.scenario_id: t for t in CRISIS_TABLES}
