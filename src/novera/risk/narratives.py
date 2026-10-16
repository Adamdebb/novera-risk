"""Forward-looking narratives: a starter set of hypothetical stories (MR-018, decision 47d).

Each narrative is a story about what could happen next, told through the move of a few headline
factors. Like a calibrated crisis (``crisis_tables``), it applies those moves and fills every other
factor with its conditional expectation under stressed correlations, but nothing here happened:
the moves are the author's judgement of a severe, plausible outcome over ``horizon_days``.

The set covers what the library left uncovered after round 47c: precious and base metals in both
directions (the bank is long metals, the fund short silver and copper against long gold), and
single names (the fund's long NVDA and short JPM, the bank's short TTE, SAP and NESN, a TEVA CDS
position). Every narrative is tagged MACRO or GEOPOLITICAL, so the library files it under
"Hypothetical forward-looking scenarios" (``category_of``).

**The sizes are drafts, not data.** They enter the library only as INFORMATION drafts from Risk
Control and run once the Head of Market Risk or the CRO approves each (four eyes, OPS-005). Read
the plausibility z-score next to each draft before approving; correct a size by proposing a new
version. Units are engine units: rates as decimals, CDS in bp, prices as fractions, ``VOL:`` in
vol points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DRAFT_NOTE = (
    "Forward-looking narrative drafted by Risk Control; the headline moves are judgement, not data. "
    "Check the plausibility z-score and the desks it hits before approving."
)


@dataclass(frozen=True)
class Narrative:
    scenario_id: str
    name: str
    narrative: str
    catalyst: str
    moves: tuple[tuple[str, float], ...]
    """(factor id, move): rates as decimals, CDS in bp, prices as fractions, ``VOL:`` in vol points."""
    tags: tuple[str, ...]
    horizon_days: int = 20
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
                f"Forward-looking narrative over {self.horizon_days} business days; "
                "other factors move with stressed correlations"
            ),
            "rules": self.rules(),
            "expand": True,
            "covariance": "STRESSED",
            "horizon_days": self.horizon_days,
            "tags": list(self.tags),
            "severity": self.severity,
            "narrative": self.narrative,
            "catalyst": self.catalyst,
            "source": "USER",
        }


SPX, NDX, SX5E, DAX, FTSE = "EQIDX:SPX", "EQIDX:NDX", "EQIDX:SX5E", "EQIDX:DAX", "EQIDX:FTSE"
US2, US10, EU10 = "IR:USD:2Y", "IR:USD:10Y", "IR:EUR:10Y"
EURUSD, USDJPY, AUDUSD, USDZAR, USDBRL = "FX:EURUSD", "FX:USDJPY", "FX:AUDUSD", "FX:USDZAR", "FX:USDBRL"
IG, HY, MAIN = "CDS:CDX.NA.IG", "CDS:CDX.NA.HY", "CDS:ITRAXX.EUR.MAIN"
VOL = "VOL:SPX:3M:1.00"
GOLD, SILVER, COPPER, ALUMINIUM = "CMD:GOLD:1M", "CMD:SILVER:1M", "CMD:COPPER:1M", "CMD:ALUMINIUM:1M"
GOLD_VOL, SILVER_VOL, COPPER_VOL = "VOL:GOLD:3M:1.00", "VOL:SILVER:3M:1.00", "VOL:COPPER:3M:1.00"
BRENT = "CMD:BRENT:1M"

NARRATIVES: tuple[Narrative, ...] = (
    # --- metals --------------------------------------------------------------------------
    Narrative(
        "gold_real_rates_unwind",
        "Gold unwind on a real-rates repricing",
        "Inflation surprises lower while growth holds up; the market prices out cuts, US real yields "
        "rise about 75bp and the dollar firms. Official-sector gold buying pauses and ETF holders "
        "sell into a crowded long, so silver falls harder than gold and metals vol jumps.",
        "Two soft inflation prints and a hawkish central-bank pivot; reserve managers pause gold buying",
        (
            (GOLD, -0.18),
            (SILVER, -0.28),
            (GOLD_VOL, 0.08),
            (SILVER_VOL, 0.12),
            (US2, 0.0050),
            (US10, 0.0075),
            (EURUSD, -0.05),
            (USDJPY, 0.04),
            (SPX, -0.04),
        ),
        ("MACRO", "COMMODITY", "RATES"),
    ),
    Narrative(
        "china_industrial_slump",
        "China industrial slump hits base metals",
        "Property and local-government credit stress deepens in China; construction and grid "
        "spending stall and stockpiles are released. Copper and aluminium fall hard, commodity "
        "currencies weaken, and gold catches a modest haven bid.",
        "A large developer default spreads to local-government financing vehicles; stimulus disappoints",
        (
            (COPPER, -0.25),
            (ALUMINIUM, -0.18),
            (SILVER, -0.12),
            (GOLD, 0.04),
            (COPPER_VOL, 0.10),
            (AUDUSD, -0.08),
            (USDZAR, 0.12),
            (USDBRL, 0.08),
            (SPX, -0.08),
            (HY, 90.0),
        ),
        ("MACRO", "COMMODITY", "FX"),
    ),
    Narrative(
        "debasement_metals_spike",
        "Central-bank credibility shock, precious metals spike",
        "Doubts over central-bank independence and a widening deficit drive a debasement trade: the "
        "front end rallies on forced cuts while the long end sells off, the dollar falls, and gold "
        "and silver spike. Short silver positions are squeezed as vol rises with the price.",
        "Political pressure forces an unscheduled cut; a long-bond auction fails",
        (
            (GOLD, 0.25),
            (SILVER, 0.40),
            (GOLD_VOL, 0.08),
            (SILVER_VOL, 0.15),
            (US2, -0.0040),
            (US10, 0.0060),
            (EURUSD, 0.07),
            (USDJPY, -0.06),
            (SPX, -0.08),
            (VOL, 0.08),
        ),
        ("MACRO", "COMMODITY", "RATES"),
        horizon_days=40,
        severity="EXTREME",
    ),
    Narrative(
        "critical_minerals_controls",
        "Export controls on critical and industrial metals",
        "A major producer restricts exports of refined copper, aluminium and silver in a trade "
        "dispute, and importers answer with tariffs. Physical shortages push industrial metals "
        "higher, short covering follows, and equities wobble on input costs.",
        "Export licensing for refined metals announced in retaliation for new tariffs",
        (
            (COPPER, 0.20),
            (ALUMINIUM, 0.25),
            (SILVER, 0.20),
            (GOLD, 0.06),
            (COPPER_VOL, 0.12),
            (SPX, -0.05),
            (SX5E, -0.06),
            (AUDUSD, 0.03),
        ),
        ("GEOPOLITICAL", "COMMODITY", "CROWDING"),
    ),
    # --- single names ------------------------------------------------------------------
    Narrative(
        "ai_capex_reversal",
        "AI capex reversal",
        "Hyperscalers cut AI capital spending guidance as returns disappoint. Chip and equipment "
        "names de-rate sharply, mega-cap tech follows, single-stock vol jumps, and the Nasdaq "
        "underperforms the broad market.",
        "Two hyperscalers cut capex guidance in the same earnings week",
        (
            ("EQ:NVDA", -0.40),
            ("EQ:ASML", -0.25),
            ("EQ:MSFT", -0.15),
            ("EQ:AAPL", -0.08),
            ("VOL:NVDA:3M:1.00", 0.20),
            (NDX, -0.18),
            (SPX, -0.10),
            (VOL, 0.10),
            (IG, 15.0),
            (US10, -0.0030),
        ),
        ("MACRO", "EQUITY", "VOLATILITY"),
    ),
    Narrative(
        "europe_rearmament_rally",
        "European rearmament and fiscal rally",
        "Europe agrees joint defence and infrastructure borrowing. Bund yields rise, the euro "
        "rallies, and European equities re-rate, led by industrials and software; defensive "
        "staples lag but still rise. Short positions in European names are squeezed.",
        "A joint EU defence fund and a German fiscal package are agreed at a summit",
        (
            (SX5E, 0.12),
            (DAX, 0.15),
            ("EQ:SIE", 0.22),
            ("EQ:SAP", 0.15),
            ("EQ:NESN", 0.06),
            ("EQ:TTE", 0.10),
            (EU10, 0.0050),
            (EURUSD, 0.06),
            (MAIN, -12.0),
        ),
        ("GEOPOLITICAL", "EQUITY", "RATES"),
    ),
    Narrative(
        "us_bank_deregulation_rally",
        "US bank deregulation and steepener rally",
        "Capital rules for large US banks are eased and the curve steepens. Bank shares re-rate "
        "and squeeze crowded shorts; credit tightens and the dollar is little changed.",
        "Final capital rule eases leverage and stress-capital buffers for large banks",
        (
            ("EQ:JPM", 0.22),
            ("EQ:HSBA", 0.10),
            ("EQ:BNP", 0.12),
            (US2, -0.0015),
            (US10, 0.0035),
            (SPX, 0.04),
            (IG, -8.0),
        ),
        ("MACRO", "EQUITY", "RATES"),
        severity="MODERATE",
    ),
    Narrative(
        "pharma_tariff_shock",
        "Tariffs on branded and generic drug imports",
        "Steep US tariffs on imported medicines hit foreign-made generics and European pharma "
        "supply chains. Leveraged generic makers' credit gaps wider; US pharma holds up better "
        "and consumer staples soften.",
        "A sector tariff on pharmaceutical imports takes effect with no exemptions",
        (
            ("CDS:TEVA", 150.0),
            ("EQ:PFE", -0.06),
            ("EQ:NESN", -0.08),
            (SX5E, -0.05),
            (DAX, -0.05),
            ("EQIDX:NKY", -0.03),
            (SPX, -0.03),
            (HY, 40.0),
            (EURUSD, -0.03),
        ),
        ("GEOPOLITICAL", "EQUITY", "CREDIT"),
        severity="MODERATE",
    ),
    Narrative(
        "gulf_supply_shock",
        "Gulf supply shock lifts oil majors",
        "A conflict closes shipping lanes in the Gulf for weeks. Brent spikes, energy majors rally "
        "against the market, gold catches a haven bid, and inflation fears lift front-end yields.",
        "Strikes on export terminals and a shipping lane closure",
        (
            (BRENT, 0.40),
            ("EQ:TTE", 0.20),
            ("EQ:SHEL", 0.18),
            ("EQ:XOM", 0.20),
            (GOLD, 0.08),
            (SPX, -0.06),
            (SX5E, -0.08),
            (US2, 0.0030),
            (VOL, 0.08),
        ),
        ("GEOPOLITICAL", "COMMODITY", "EQUITY"),
    ),
)

NARRATIVE_BY_ID = {n.scenario_id: n for n in NARRATIVES}
