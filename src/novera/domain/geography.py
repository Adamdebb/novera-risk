"""Geography hierarchy: currency pair -> currency -> country -> region (decision round 44).

A currency pair attaches to its less liquid currency, the one with the higher liquidity score
(USD 1, EUR 2, JPY 3, ...): EURUSD to EUR, USDJPY to JPY, EURGBP to GBP, USDTRY to TRY. A
currency attaches to its issuing country (EUR to the Euro area), a country to its region. An FX
trade takes the currency its pair attaches to; every other trade takes its own currency.

The table itself is generated (``geography_data.py``, from ``scripts/build_geography.py``) and
stored in the database as reference data; reports read the stored copy.
"""

from __future__ import annotations

from dataclasses import dataclass

from novera.domain import geography_data as data


def split_pair(pair: str) -> tuple[str, str]:
    """``EUR/USD``, ``EURUSD`` or ``FX:EURUSD`` -> (EUR, USD)."""
    p = pair.removeprefix("FX:").replace("/", "").upper()
    if len(p) != 6 or not p.isalpha():
        raise ValueError(f"not a currency pair: {pair!r}")
    return p[:3], p[3:]


@dataclass(frozen=True)
class Geography:
    regions: dict[str, str]  # code -> name, in reporting order
    countries: dict[str, tuple[str, str, str, bool]]  # code -> (name, region, currency, is union)
    currencies: dict[str, tuple[str, str, int]]  # code -> (name, issuing country, liquidity score)

    @classmethod
    def static(cls) -> Geography:
        """The generated table, as stored by ``seed``."""
        return cls(
            dict(data.REGIONS),
            {c[0]: (c[1], c[2], c[3], c[4]) for c in data.COUNTRIES},
            {c[0]: (c[1], c[2], c[3]) for c in data.CURRENCIES},
        )

    def score(self, ccy: str) -> int:
        """Liquidity score; a code outside the table ranks after every listed currency."""
        return self.currencies[ccy][2] if ccy in self.currencies else len(self.currencies) + 1

    def pair_currency(self, pair: str) -> str:
        """The currency a pair attaches to: the higher liquidity score (the less liquid one)."""
        a, b = split_pair(pair)
        return a if (self.score(a), a) > (self.score(b), b) else b

    def country(self, ccy: str) -> str | None:
        return self.currencies[ccy][1] if ccy in self.currencies else None

    def region(self, ccy: str) -> str | None:
        c = self.country(ccy)
        return self.countries[c][1] if c in self.countries else None

    def describe(self, ccy: str | None) -> dict[str, str | None]:
        """Country and region codes and names for a currency (None when it is not listed)."""
        country = self.country(ccy) if ccy else None
        region = self.countries[country][1] if country in self.countries else None
        return {
            "country": country,
            "country_name": self.countries[country][0] if country in self.countries else None,
            "region": region,
            "region_name": self.regions.get(region) if region else None,
        }


def seed_geography(repo) -> Geography:
    """Store the static table, with the currency pairs of the risk-factor universe and of the
    stored FX trades."""
    geo = Geography.static()
    pairs = {
        f.factor_id.removeprefix("FX:") for f in repo.load_risk_factors() if f.factor_id.startswith("FX:")
    }
    for snap_id, *_ in repo.list_portfolio_snapshots():
        try:
            snap = repo.load_portfolio_snapshot(snap_id)
        except RuntimeError:  # a snapshot stored under an older trade model; its pairs are skipped
            continue
        pairs |= {
            "".join(split_pair(t.instrument.pair)) for t in snap.trades if hasattr(t.instrument, "pair")
        }
    repo.save_geography(geo, sorted(pairs))
    return geo
