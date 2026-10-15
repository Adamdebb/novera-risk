"""Build the static geography table: regions, countries, currencies and currency liquidity
(decision round 44). Run once when the source data or the rules change:

    uv run --with babel python scripts/build_geography.py

It writes ``src/novera/domain/geography_data.py``, which the platform reads; babel (the Unicode
CLDR data: ISO 3166 territories, ISO 4217 currencies, which currency is legal tender where) is
needed only here, not at run time.

Rules:
- Currencies: every ISO 4217 currency that is legal tender somewhere today (no fund codes, metals,
  SDR or test codes).
- A currency's country is its issuer: the territory whose ISO 3166 code is the currency code's
  first two letters (USD -> US), or a monetary union for a shared currency (EUR, XOF, XAF, XCD,
  XPF, XCG).
- Regions: Developed Markets is the issuers of USD, EUR, JPY, GBP, AUD, NZD, CAD, CHF, NOK, SEK and
  DKK, the euro-area members, and territories of those states that use the sovereign's currency;
  every other country goes by geography. North Asia excludes Japan. Central Asia, the Caucasus,
  Turkey and Iceland count as CEE; the Pacific islands as South East Asia; the Caribbean and
  Central America as Latin America.
- Liquidity score: the BIS Triennial Survey 2022 ranking by FX turnover (1 = most traded), then
  every other currency in alphabetical order, so each score is unique.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from babel import Locale
from babel.numbers import get_territory_currencies

OUT = Path(__file__).resolve().parents[1] / "src" / "novera" / "domain" / "geography_data.py"

REGIONS = (
    ("DM", "Developed Markets"),
    ("NORTH_ASIA", "North Asia (ex Japan)"),
    ("SOUTH_EAST_ASIA", "South East Asia"),
    ("SOUTH_ASIA", "South Asia"),
    ("CEE", "Central and Eastern Europe"),
    ("MIDDLE_EAST", "Middle East"),
    ("AFRICA", "Africa"),
    ("LATIN_AMERICA", "Latin America"),
)
DM_CURRENCIES = ("USD", "EUR", "JPY", "GBP", "AUD", "NZD", "CAD", "CHF", "NOK", "SEK", "DKK")
EURO_AREA = (
    "AT BE BG HR CY EE FI FR DE GR IE IT LV LT LU MT NL PT SK SI ES".split()
)  # Bulgaria from 1 January 2026
OVERRIDES = {"BG": ["EUR"]}  # CLDR in babel 2.18 still lists BGN for Bulgaria

BY_REGION: dict[str, str] = {
    # Developed Markets: DM issuers, euro area, European euro users, territories of DM states
    "DM": "US JP GB AU NZ CA CH NO SE DK " + " ".join(EURO_AREA) + " AD MC SM VA AX LI GL FO SJ BV "
    "GG IM JE GI CQ GS IO UM PR VI GU AS MP HM CC CX NF TF RE YT GF GP MQ BL MF PM CP BQ EA IC TA DG",
    "NORTH_ASIA": "CN HK MO TW KR KP MN",
    "SOUTH_EAST_ASIA": "SG MY ID TH PH VN KH LA MM BN TL "
    "FJ PG SB VU WS TO KI NR TV MH FM PW CK NU TK PN NC PF WF",
    "SOUTH_ASIA": "IN PK BD LK NP BT MV AF",
    "CEE": "PL CZ HU RO RS BA ME MK AL XK MD UA BY RU GE AM AZ KZ UZ KG TJ TM TR IS",
    "MIDDLE_EAST": "AE SA QA KW BH OM YE IQ IR SY JO LB IL PS",
    "AFRICA": "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY "
    "MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW EH SH AC",
    "LATIN_AMERICA": "MX GT BZ SV HN NI CR PA CU DO HT JM BS BB TT AG DM GD KN LC VC AW CW SX BM KY VG TC AI "
    "MS FK CO VE EC PE BO CL AR UY PY BR GY SR",
}
UNIONS = {  # code: (name, region, currency)
    "EMU": ("Euro area", "DM", "EUR"),
    "WAEMU": ("West African Economic and Monetary Union", "AFRICA", "XOF"),
    "CEMAC": ("Central African Economic and Monetary Community", "AFRICA", "XAF"),
    "ECCU": ("Eastern Caribbean Currency Union", "LATIN_AMERICA", "XCD"),
    "CFP": ("CFP franc zone (French Pacific)", "SOUTH_EAST_ASIA", "XPF"),
    "CWSX": ("Curaçao and Sint Maarten", "LATIN_AMERICA", "XCG"),
}
BIS_2022 = (
    "USD EUR JPY GBP CNY AUD CAD CHF HKD SGD SEK KRW NOK NZD INR MXN TWD ZAR BRL DKK PLN THB ILS IDR "
    "CZK AED TRY HUF CLP SAR PHP MYR COP RUB RON PEN BHD ARS"
).split()  # BGN dropped: replaced by EUR in 2026


def main() -> None:
    en = Locale("en")
    today = date.today()
    region_of = {c: r for r, codes in BY_REGION.items() for c in codes.split()}
    countries = []
    users: dict[str, list[str]] = {}
    unassigned: list[str] = []
    for code, name in sorted(en.territories.items()):
        if len(code) != 2 or not code.isalpha() or code in ("ZZ", "EU", "EZ", "UN", "QO", "XA", "XB"):
            continue
        ccys = OVERRIDES.get(code) or get_territory_currencies(code, today, tender=True, non_tender=False)
        if not ccys:
            continue  # Antarctica and other territories with no currency
        if code not in region_of:
            unassigned.append(f"{code} {name} {ccys}")
            continue
        countries.append((code, name, region_of[code], ccys[0], False))
        for c in ccys:
            users.setdefault(c, []).append(code)
    if unassigned:
        raise SystemExit("no region for:\n" + "\n".join(unassigned))
    for code, (name, region, ccy) in UNIONS.items():
        countries.append((code, name, region, ccy, True))
    union_of = {ccy: code for code, (_, _, ccy) in UNIONS.items()}
    country_codes = {c[0] for c in countries}
    currencies = []
    for ccy in sorted(users):
        if ccy in union_of:
            issuer = union_of[ccy]
        elif ccy[:2] in users[ccy]:
            issuer = ccy[:2]
        elif len(users[ccy]) == 1:
            issuer = users[ccy][0]
        else:
            raise SystemExit(f"no issuer for {ccy}: {users[ccy]}")
        assert issuer in country_codes, (ccy, issuer)
        currencies.append([ccy, en.currencies.get(ccy, ccy), issuer])
    rest = sorted(c for c, _, _ in currencies if c not in BIS_2022)
    missing = [c for c in BIS_2022 if c not in users]
    if missing:
        raise SystemExit(f"BIS currencies not in circulation: {missing}")
    score = {c: i + 1 for i, c in enumerate([*BIS_2022, *rest])}
    for row in currencies:
        row.append(score[row[0]])
    for c in DM_CURRENCIES:
        issuer = next(r[2] for r in currencies if r[0] == c)
        assert region_of.get(issuer, UNIONS.get(issuer, ("", "DM"))[1]) == "DM", c
    lines = [
        '"""Static geography table, generated by scripts/build_geography.py (decision round 44).',
        "Do not edit by hand: change the rules there and run it again.",
        f"Source: Unicode CLDR via babel, ISO 3166 and ISO 4217, legal tender on {today}; liquidity",
        'from the BIS Triennial Survey 2022 ranking, the rest alphabetical."""',
        "",
        "REGIONS: tuple[tuple[str, str], ...] = (",
        *[f"    ({c!r}, {n!r})," for c, n in REGIONS],
        ")",
        "",
        "# (country code, name, region, currency, is a monetary union)",
        "COUNTRIES: tuple[tuple[str, str, str, str, bool], ...] = (",
        *[f"    {row!r}," for row in sorted(countries, key=lambda r: (r[4], r[0]))],
        ")",
        "",
        "# (currency code, name, issuing country, liquidity score: 1 = most liquid)",
        "CURRENCIES: tuple[tuple[str, str, str, int], ...] = (",
        *[f"    {tuple(row)!r}," for row in sorted(currencies, key=lambda r: r[3])],
        ")",
        "",
        f"DM_CURRENCIES: tuple[str, ...] = {DM_CURRENCIES!r}",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    import subprocess

    subprocess.run(["ruff", "format", str(OUT)], check=False, capture_output=True)
    print(f"{len(REGIONS)} regions, {len(countries)} countries, {len(currencies)} currencies -> {OUT}")


if __name__ == "__main__":
    main()
