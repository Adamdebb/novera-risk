"""Geography hierarchy: currency pair -> currency -> country -> region (decision round 44)."""

import pytest

from novera.domain import geography_data as data
from novera.domain.geography import Geography, split_pair


@pytest.fixture(scope="module")
def geo() -> Geography:
    return Geography.static()


def test_static_table_is_complete_and_consistent(geo):
    assert list(geo.regions) == [
        "DM",
        "NORTH_ASIA",
        "SOUTH_EAST_ASIA",
        "SOUTH_ASIA",
        "CEE",
        "MIDDLE_EAST",
        "AFRICA",
        "LATIN_AMERICA",
    ]
    assert len(geo.currencies) >= 150 and len(geo.countries) >= 250
    for code, (_, country, _) in geo.currencies.items():
        assert country in geo.countries, code  # every currency has an issuer
    for code, (_, region, ccy, _) in geo.countries.items():
        assert region in geo.regions and ccy in geo.currencies, code
    scores = sorted(s for _, _, s in geo.currencies.values())
    assert scores == list(range(1, len(scores) + 1))  # unique, 1 = most liquid
    assert [c for c, *_ in data.CURRENCIES[:5]] == ["USD", "EUR", "JPY", "GBP", "CNY"]
    for ccy in data.DM_CURRENCIES:
        assert geo.region(ccy) == "DM", ccy
    assert geo.region("CNY") == geo.region("KRW") == "NORTH_ASIA"
    assert geo.region("JPY") == "DM"  # North Asia excludes Japan
    assert geo.region("PLN") == geo.region("TRY") == "CEE"
    assert geo.region("INR") == "SOUTH_ASIA" and geo.region("SGD") == "SOUTH_EAST_ASIA"
    assert geo.region("SAR") == "MIDDLE_EAST" and geo.region("ZAR") == "AFRICA"
    assert geo.region("BRL") == "LATIN_AMERICA"


def test_shared_currencies_attach_to_monetary_unions(geo):
    assert geo.country("EUR") == "EMU" and geo.countries["EMU"][3]
    assert geo.country("XOF") == "WAEMU" and geo.region("XOF") == "AFRICA"
    assert geo.country("XCD") == "ECCU" and geo.region("XCD") == "LATIN_AMERICA"
    assert geo.countries["DE"][2] == "EUR" and geo.countries["DE"][1] == "DM"
    assert geo.countries["BG"][2] == "EUR" and "BGN" not in geo.currencies  # euro from 2026
    assert geo.countries["SK"][1] == "DM"  # a euro-area member is Developed Markets
    assert geo.countries["EC"][1] == "LATIN_AMERICA"  # uses USD but does not issue it


@pytest.mark.parametrize(
    ("pair", "ccy"),
    [
        ("EURUSD", "EUR"),
        ("USDJPY", "JPY"),
        ("JPYUSD", "JPY"),
        ("EURGBP", "GBP"),
        ("EUR/CHF", "CHF"),
        ("FX:USDTRY", "TRY"),
        ("AUDNZD", "NZD"),
        ("USDCNY", "CNY"),
    ],
)
def test_a_pair_attaches_to_its_less_liquid_currency(geo, pair, ccy):
    assert geo.pair_currency(pair) == ccy


def test_pair_parsing_and_unknown_codes(geo):
    assert split_pair("eur/usd") == ("EUR", "USD")
    with pytest.raises(ValueError):
        split_pair("EURUS")
    assert geo.pair_currency("USDXYZ") == "XYZ"  # an unlisted code ranks after every listed one
    assert geo.describe("XYZ") == {"country": None, "country_name": None, "region": None, "region_name": None}
