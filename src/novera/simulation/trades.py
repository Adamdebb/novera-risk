"""Trade generator for the simulated bank.

Reproducible for a given seed. Produces a realistic mix per desk and deliberately plants
the problems listed in ``docs/03-roadmap.md`` so the platform has something to detect.
Each planted problem is recorded in ``GeneratedPortfolio.injections`` for the demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, ConfigDict

from novera.domain import (
    BuySell,
    ClearingType,
    OptionType,
    Organisation,
    PortfolioSnapshot,
    ProductType,
    SwapSide,
    Trade,
    TradeStatus,
)
from novera.market_data.history import MarketHistory
from novera.market_data.snapshot import MarketSnapshot
from novera.pricing import PRICERS
from novera.simulation import instruments as inst
from novera.simulation import reference_levels as ref
from novera.simulation.organisation import CounterpartyUniverse


class Injection(BaseModel):
    """A deliberately planted problem and the trades that carry it."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    trade_ids: tuple[str, ...]
    expected_detection: str


@dataclass
class GeneratedPortfolio:
    snapshot: PortfolioSnapshot
    injections: list[Injection] = field(default_factory=list)


@dataclass(frozen=True)
class TradeGeneratorConfig:
    business_date: date
    n_trades: int = 1500
    seed: int = 42
    inject_problems: bool = True
    market_history: MarketHistory | None = None
    template: Template = None  # type: ignore[assignment]  # defaults to BANK_TEMPLATE in __post_init__
    """When given, every trade is struck at fair market on its trade date (plus execution
    noise), so inception P&L is small and later P&L is genuine. Without it, prices come
    from static reference levels."""

    def __post_init__(self) -> None:
        if self.template is None:
            object.__setattr__(self, "template", BANK_TEMPLATE)


# Desk -> product recipe weights. Keys are recipe names implemented in _Recipes.
_DESK_MIX: dict[str, dict[str, float]] = {
    "USD_RATES": {"govt_bond": 0.4, "swap": 0.6},
    "EUR_RATES": {"govt_bond": 0.5, "swap": 0.5},
    "GBP_RATES": {"govt_bond": 0.5, "swap": 0.5},
    "JPY_RATES": {"govt_bond": 0.5, "swap": 0.5},
    "EM_RATES": {"govt_bond": 0.6, "swap": 0.4},
    "G10_FX": {"fx_spot": 0.2, "fx_forward": 0.5, "fx_option": 0.3},
    "EM_FX": {"fx_forward": 0.7, "fx_option": 0.3},
    "INDEX_EQ": {"index_future": 0.6, "index_option": 0.4},
    "SINGLE_NAME_EQ": {"cash_equity": 0.7, "equity_option": 0.3},
    "IG_CREDIT": {"cds_index": 1.0},
    "HY_CREDIT": {"cds_index": 1.0},
    "ENERGY": {"commodity_future": 1.0},
    "METALS": {"commodity_future": 1.0},
    "DIGITAL": {"crypto_spot": 1.0},
}
# Relative desk activity (share of trades).
_DESK_WEIGHT: dict[str, float] = {
    "USD_RATES": 0.16,
    "EUR_RATES": 0.10,
    "GBP_RATES": 0.06,
    "JPY_RATES": 0.05,
    "EM_RATES": 0.04,
    "G10_FX": 0.12,
    "EM_FX": 0.06,
    "INDEX_EQ": 0.08,
    "SINGLE_NAME_EQ": 0.12,
    "IG_CREDIT": 0.05,
    "HY_CREDIT": 0.04,
    "ENERGY": 0.05,
    "METALS": 0.04,
    "DIGITAL": 0.03,
}
_DESK_CURRENCY: dict[str, list[str]] = {
    "USD_RATES": ["USD"],
    "EUR_RATES": ["EUR"],
    "GBP_RATES": ["GBP"],
    "JPY_RATES": ["JPY"],
    "EM_RATES": ["MXN", "BRL", "ZAR"],
}
_DESK_FX_PAIRS: dict[str, list[str]] = {
    "G10_FX": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "USD/CAD", "NZD/USD", "EUR/GBP"],
    "EM_FX": ["USD/MXN", "USD/BRL", "USD/ZAR", "USD/INR", "USD/TRY", "USD/SGD"],
}
_DESK_INDICES: dict[str, list[str]] = {
    "EU_INDEX": ["SX5E", "DAX", "FTSE"],
    "US_INDEX": ["SPX", "NDX"],
    "INDEX_VOL": ["SPX", "SX5E", "NKY"],
}
_BOOK_EQUITIES: dict[str, list[str]] = {
    "US_CASH_EQ": [t for t, v in ref.EQUITIES.items() if v[3] == "US"],
    "EU_CASH_EQ": [t for t, v in ref.EQUITIES.items() if v[3] != "US"],
    "SN_OPTIONS": [t for t, v in ref.EQUITIES.items() if v[3] == "US"],
}
_BOOK_CDS: dict[str, str] = {
    "CDX_IG": "CDX.NA.IG",
    "ITRAXX_MAIN": "ITRAXX.EUR.MAIN",
    "CDX_HY": "CDX.NA.HY",
    "ITRAXX_XOVER": "ITRAXX.EUR.XOVER",
}
_BOOK_COMMODITIES: dict[str, list[str]] = {
    "CRUDE": ["BRENT", "WTI"],
    "NATGAS": ["NATGAS"],
    "PRECIOUS": ["GOLD", "SILVER"],
    "BASE_METALS": ["COPPER", "ALUMINIUM"],
}
_EXCHANGE_CPTY = {
    "CME": "EXCH_CME",
    "EUREX": "EXCH_EUREX",
    "ICE": "EXCH_ICE",
    "NYSE": "EXCH_NYSE",
    "NASDAQ": "EXCH_NYSE",
    "XETRA": "EXCH_XETRA",
    "EURONEXT": "EXCH_XETRA",
    "SIX": "EXCH_XETRA",
    "LSE": "EXCH_XETRA",
    "OSE": "EXCH_CME",
    "COINBASE": "EXCH_COINBASE",
}


@dataclass(frozen=True)
class Template:
    """Everything the generator needs to know about an organisation's desks and books."""

    name: str
    desk_mix: dict[str, dict[str, float]]
    desk_weight: dict[str, float]
    desk_currency: dict[str, list[str]]
    desk_fx_pairs: dict[str, list[str]]
    book_indices: dict[str, list[str]]
    book_equities: dict[str, list[str]]
    book_cds: dict[str, str]
    book_commodities: dict[str, list[str]]
    default_index_book: str
    index_option_book: str
    default_equity_book: str
    equity_option_book: str
    size_scale: float = 1.0
    injector: str = "bank"
    preferred_counterparty: str | None = "BANK_A"  # over-weighted in bilateral allocation
    nav: float | None = None  # fund templates: sizes planted problems relative to NAV


BANK_TEMPLATE = Template(
    "bank",
    _DESK_MIX,
    _DESK_WEIGHT,
    _DESK_CURRENCY,
    _DESK_FX_PAIRS,
    _DESK_INDICES,
    _BOOK_EQUITIES,
    _BOOK_CDS,
    _BOOK_COMMODITIES,
    "US_INDEX",
    "INDEX_VOL",
    "US_CASH_EQ",
    "SN_OPTIONS",
)


class _Gen:
    def __init__(self, org: Organisation, cp: CounterpartyUniverse, cfg: TradeGeneratorConfig):
        self.t = cfg.template
        self.org, self.cp, self.cfg = org, cp, cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.bd = cfg.business_date
        self.n = 0
        self.bonds = {c: inst.government_bonds(c, self.bd) for c in ref.BOND_CURRENCIES}
        # Bank A is over-weighted on purpose (counterparty concentration).
        bilateral = self.cp.bilateral
        w = np.array([4.0 if c.counterparty_id == self.t.preferred_counterparty else 1.0 for c in bilateral])
        self._bilateral_ids = [c.counterparty_id for c in bilateral]
        self._bilateral_w = w / w.sum()

    # --- helpers ---------------------------------------------------------------------
    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n:06d}"

    def _trade_date(self, max_days_back: int = 500) -> date:
        d = self.bd - relativedelta(days=int(self.rng.integers(0, max_days_back)))
        while d.weekday() > 4:
            d -= relativedelta(days=1)
        return d

    def _lognormal(self, low: float, high: float) -> float:
        return float(np.exp(self.rng.uniform(np.log(low), np.log(high)))) * self.t.size_scale

    def _round(self, x: float, step: float) -> float:
        return float(max(step, round(x / step) * step))

    def _direction(self) -> BuySell:
        return BuySell.BUY if self.rng.random() < 0.5 else BuySell.SELL

    def _book_and_trader(self, desk_id: str) -> tuple[str, str]:
        books = [b for b in self.org.books if b.desk_id == desk_id]
        traders = [t for t in self.org.traders if t.desk_id == desk_id]
        return (
            books[int(self.rng.integers(len(books)))].book_id,
            traders[int(self.rng.integers(len(traders)))].trader_id,
        )

    def _bilateral(self, book_id: str, prefer: str | None = None) -> tuple[str, str]:
        cid = prefer or str(self.rng.choice(self._bilateral_ids, p=self._bilateral_w))
        le = self.org.book(book_id).legal_entity_id
        ns = self.cp.netting_set_for(cid, le)
        assert ns is not None, (cid, le)
        return cid, ns.netting_set_id

    def _otc(self, book_id: str, ccp: str | None, cleared_share: float) -> dict:
        """Counterparty/clearing fields for an OTC trade."""
        if ccp and self.rng.random() < cleared_share:
            return {"counterparty_id": ccp, "clearing": ClearingType.CLEARED, "netting_set_id": None}
        cid, ns = self._bilateral(book_id)
        return {"counterparty_id": cid, "clearing": ClearingType.BILATERAL, "netting_set_id": ns}

    def _listed(self, exchange: str) -> dict:
        return {
            "counterparty_id": _EXCHANGE_CPTY[exchange],
            "clearing": ClearingType.EXCHANGE,
            "netting_set_id": None,
        }

    @staticmethod
    def _restrike(trade: Trade, snap: MarketSnapshot) -> Trade:
        """Option strikes were drawn around reference levels; move them to the same relative
        moneyness against the market on the trade date so the book is not all deep ITM/OTM."""
        ins = trade.instrument
        pt = trade.product_type
        if pt is ProductType.FX_OPTION:
            ref_spot = ref.FX_SPOT[ins.pair][0]
            spot = snap.fx_spot(ins.pair)
            strike = round(ins.strike * spot / ref_spot, 4)
        elif pt is ProductType.EQUITY_OPTION:
            u = ins.underlying
            if u in ref.EQUITY_INDICES:
                ref_spot, spot = ref.EQUITY_INDICES[u][2], snap.index_level(u)
                step = 25 if spot > 1000 else 5
            else:
                ref_spot, spot = ref.EQUITIES[u][4], snap.equity_spot(u)
                step = 5 if spot > 50 else 1
            strike = max(step, round(ins.strike * spot / ref_spot / step) * step)
        else:
            return trade
        new_ins = ins.model_copy(update={"strike": strike})
        new_ins = new_ins.model_copy(
            update={
                "instrument_id": ins.instrument_id.replace(
                    f"{ins.strike:.4f}" if pt is ProductType.FX_OPTION else f"{ins.strike:.0f}",
                    f"{strike:.4f}" if pt is ProductType.FX_OPTION else f"{strike:.0f}",
                )
            }
        )
        return trade.model_copy(update={"instrument": new_ins})

    def _fair(self, trade: Trade, noise_bp: float = 5.0) -> Trade:
        """Re-strike the trade at fair market on its trade date, if history is available.

        The fair level is what makes inception PV zero: dirty price for bonds, par rate
        for swaps, CIP forward for FX forwards, unit premium for options, forward for
        futures, spot for cash products, market spread for CDS. A small execution noise
        (basis points of the level) is added so books do not start at exactly zero P&L.
        """
        hist = self.cfg.market_history
        if hist is None:
            return trade
        try:
            snap = hist.snapshot_at(trade.trade_date)
            trade = self._restrike(trade, snap)
            r = PRICERS[trade.product_type](trade, snap, snap.as_of)
        except (KeyError, ValueError):
            return trade
        d = r.details
        pt = trade.product_type
        if pt is ProductType.GOVERNMENT_BOND:
            fair = d["dirty_price"]
        elif pt is ProductType.INTEREST_RATE_SWAP:
            fair = d["par_rate"]
            trade = trade.model_copy(
                update={"instrument": trade.instrument.model_copy(update={"fixed_rate": round(fair, 5)})}
            )
        elif pt is ProductType.FX_FORWARD:
            fair = d["forward"]
            trade = trade.model_copy(
                update={"instrument": trade.instrument.model_copy(update={"forward_rate": round(fair, 5)})}
            )
        elif pt is ProductType.FX_SPOT:
            fair = d["spot"]
        elif pt in (ProductType.FX_OPTION, ProductType.EQUITY_OPTION):
            fair = d["unit_price"]
        elif pt in (ProductType.EQUITY_INDEX_FUTURE, ProductType.COMMODITY_FUTURE):
            fair = d["forward"]
        elif pt is ProductType.CDS_INDEX:
            fair = d["spread_bp"] / 1e4
        else:  # cash equity, crypto
            fair = d["spot"]
        noise = 1.0 + float(self.rng.normal(0, noise_bp / 1e4))
        return trade.model_copy(update={"trade_price": float(fair * noise)})

    # --- recipes ---------------------------------------------------------------------
    def govt_bond(self, desk_id: str) -> Trade:
        ccy = str(self.rng.choice(self.t.desk_currency[desk_id]))
        bond = self.bonds[ccy][int(self.rng.integers(4))]
        book, trader = self._book_and_trader(desk_id)
        td = self._trade_date()
        return Trade(
            trade_id=self._id("BOND"),
            instrument=bond,
            direction=self._direction(),
            quantity=self._round(self._lognormal(5e6, 150e6), 1e6),
            trade_price=round(float(self.rng.normal(99.5, 2.0)), 3),
            trade_date=td,
            settlement_date=td + relativedelta(days=1),
            book_id=book,
            trader_id=trader,
            **self._listed("CME" if ccy == "USD" else "EUREX"),
        )

    def swap(
        self,
        desk_id: str,
        tenor: str | None = None,
        notional: float | None = None,
        side: SwapSide | None = None,
        book_id: str | None = None,
        prefer_cpty: str | None = None,
        max_days_back: int = 500,
    ) -> Trade:
        ccy = str(self.rng.choice(self.t.desk_currency[desk_id]))
        tenor = tenor or str(self.rng.choice(inst.SWAP_TENORS, p=[0.3, 0.3, 0.3, 0.1]))
        td = self._trade_date(max_days_back)
        eff = td + relativedelta(days=2)
        rate = round(ref.SWAP_CURVES[ccy][tenor] + float(self.rng.normal(0, 0.003)), 4)
        s = inst.swap(ccy, tenor, eff, rate)
        book, trader = self._book_and_trader(desk_id)
        book = book_id or book
        if prefer_cpty:
            cid, ns = self._bilateral(book, prefer_cpty)
            cp = {"counterparty_id": cid, "clearing": ClearingType.BILATERAL, "netting_set_id": ns}
        else:
            cp = self._otc(book, "CCP_LCH", cleared_share=0.7)
        return Trade(
            trade_id=self._id("IRS"),
            instrument=s,
            direction=BuySell.BUY,
            swap_side=side or (SwapSide.PAY_FIXED if self.rng.random() < 0.5 else SwapSide.RECEIVE_FIXED),
            quantity=notional or self._round(self._lognormal(25e6, 500e6), 5e6),
            trade_price=rate,
            trade_date=td,
            settlement_date=eff,
            book_id=book,
            trader_id=trader,
            **cp,
        )

    def fx_spot(self, desk_id: str) -> Trade:
        pair = str(self.rng.choice(self.t.desk_fx_pairs[desk_id]))
        spot, _ = ref.FX_SPOT[pair]
        book, trader = self._book_and_trader(desk_id)
        td = self._trade_date(3)
        return Trade(
            trade_id=self._id("FXS"),
            instrument=inst.fx_spot(pair),
            direction=self._direction(),
            quantity=self._round(self._lognormal(5e6, 200e6), 1e6),
            trade_price=round(spot * (1 + float(self.rng.normal(0, 0.002))), 5),
            trade_date=td,
            settlement_date=td + relativedelta(days=2),
            book_id=book,
            trader_id=trader,
            **self._otc(book, None, 0.0),
        )

    def fx_forward(
        self,
        desk_id: str,
        pair: str | None = None,
        notional: float | None = None,
        book_id: str | None = None,
        prefer_cpty: str | None = None,
        direction: BuySell | None = None,
    ) -> Trade:
        pair = pair or str(self.rng.choice(self.t.desk_fx_pairs[desk_id]))
        spot, _ = ref.FX_SPOT[pair]
        book, trader = self._book_and_trader(desk_id)
        book = book_id or book
        months = int(self.rng.choice([1, 3, 6, 12, 24], p=[0.25, 0.3, 0.25, 0.15, 0.05]))
        td = self._trade_date(max(int(months * 30 * 0.85), 5))
        settle = td + relativedelta(months=months)
        base, quote = pair[:3], pair[4:]
        rb = ref.SWAP_CURVES.get(base, ref.SWAP_CURVES["USD"])["1Y"]
        rq = ref.SWAP_CURVES.get(quote, ref.SWAP_CURVES["USD"])["1Y"]
        fwd = spot * (1 + rq * months / 12) / (1 + rb * months / 12)
        fwd = round(fwd * (1 + float(self.rng.normal(0, 0.003))), 5)
        if prefer_cpty:
            cid, ns = self._bilateral(book, prefer_cpty)
            cp = {"counterparty_id": cid, "clearing": ClearingType.BILATERAL, "netting_set_id": ns}
        else:
            cp = self._otc(book, None, 0.0)
        return Trade(
            trade_id=self._id("FXF"),
            instrument=inst.fx_forward(pair, settle, fwd),
            direction=direction or self._direction(),
            quantity=notional or self._round(self._lognormal(5e6, 250e6), 1e6),
            trade_price=fwd,
            trade_date=td,
            settlement_date=settle,
            book_id=book,
            trader_id=trader,
            **cp,
        )

    def fx_option(self, desk_id: str) -> Trade:
        pair = str(self.rng.choice(self.t.desk_fx_pairs[desk_id]))
        spot, vol = ref.FX_SPOT[pair]
        book, trader = self._book_and_trader(desk_id)
        td = self._trade_date(200)
        months = int(self.rng.choice([1, 3, 6, 12]))
        expiry = max(td + relativedelta(months=months), self.bd + relativedelta(days=30))
        kind = OptionType.CALL if self.rng.random() < 0.5 else OptionType.PUT
        strike = round(spot * float(np.exp(self.rng.normal(0, vol * 0.5))), 4)
        premium = round(spot * vol * np.sqrt(months / 12) * 0.4, 5)
        return Trade(
            trade_id=self._id("FXO"),
            instrument=inst.fx_option(pair, expiry, strike, kind),
            direction=self._direction(),
            quantity=self._round(self._lognormal(10e6, 150e6), 1e6),
            trade_price=premium,
            trade_date=td,
            settlement_date=td + relativedelta(days=2),
            book_id=book,
            trader_id=trader,
            **self._otc(book, None, 0.0),
        )

    def index_future(self, desk_id: str) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        if book not in self.t.book_indices:
            book = self.t.default_index_book
        index = str(self.rng.choice(self.t.book_indices[book]))
        exch, _, level, _, _ = ref.EQUITY_INDICES[index]
        fut = inst.index_futures(index, self.bd)[int(self.rng.choice([0, 0, 1, 2]))]
        td = self._trade_date(60)
        return Trade(
            trade_id=self._id("EIF"),
            instrument=fut,
            direction=self._direction(),
            quantity=self._round(self._lognormal(50, 2500), 10),
            trade_price=round(level * (1 + float(self.rng.normal(0, 0.01))), 2),
            trade_date=td,
            settlement_date=td,
            book_id=book,
            trader_id=trader,
            **self._listed(exch),
        )

    def index_option(self, desk_id: str) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        book = self.t.index_option_book
        index = str(self.rng.choice(self.t.book_indices[book]))
        exch, ccy, level, vol, mult = ref.EQUITY_INDICES[index]
        td = self._trade_date(120)
        months = int(self.rng.choice([1, 2, 3, 6, 12]))
        expiry = max(td + relativedelta(months=months), self.bd + relativedelta(days=30))
        kind = OptionType.CALL if self.rng.random() < 0.4 else OptionType.PUT
        strike = self._round(level * float(np.exp(self.rng.normal(0, vol * 0.4))), 25 if level > 1000 else 5)
        opt = inst.equity_option(index, expiry, strike, kind, ccy, exch, mult)
        return Trade(
            trade_id=self._id("EIO"),
            instrument=opt,
            direction=self._direction(),
            quantity=self._round(self._lognormal(20, 600), 10),
            trade_price=round(level * vol * np.sqrt(months / 12) * 0.4, 2),
            trade_date=td,
            settlement_date=td + relativedelta(days=1),
            book_id=book,
            trader_id=trader,
            **self._listed(exch),
        )

    def cash_equity(self, desk_id: str) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        if book not in self.t.book_equities or book == self.t.equity_option_book:
            book = self.t.default_equity_book
        ticker = str(self.rng.choice(self.t.book_equities[book]))
        exch, _, _, _, price, _ = ref.EQUITIES[ticker]
        td = self._trade_date(400)
        return Trade(
            trade_id=self._id("EQ"),
            instrument=inst.cash_equity(ticker),
            direction=self._direction(),
            quantity=self._round(self._lognormal(5_000, 600_000), 100),
            trade_price=round(price * (1 + float(self.rng.normal(0, 0.05))), 2),
            trade_date=td,
            settlement_date=td + relativedelta(days=2),
            book_id=book,
            trader_id=trader,
            **self._listed(exch),
        )

    def equity_option(self, desk_id: str) -> Trade:
        _, trader = self._book_and_trader(desk_id)
        book = self.t.equity_option_book
        ticker = str(self.rng.choice(self.t.book_equities[book]))
        exch, ccy, _, _, price, vol = ref.EQUITIES[ticker]
        td = self._trade_date(120)
        months = int(self.rng.choice([1, 2, 3, 6, 12]))
        expiry = max(td + relativedelta(months=months), self.bd + relativedelta(days=30))
        kind = OptionType.CALL if self.rng.random() < 0.5 else OptionType.PUT
        strike = self._round(price * float(np.exp(self.rng.normal(0, vol * 0.4))), 5 if price > 50 else 1)
        opt = inst.equity_option(ticker, expiry, strike, kind, ccy, exch)
        return Trade(
            trade_id=self._id("EQO"),
            instrument=opt,
            direction=self._direction(),
            quantity=self._round(self._lognormal(50, 2500), 10),
            trade_price=round(price * vol * np.sqrt(months / 12) * 0.4, 2),
            trade_date=td,
            settlement_date=td + relativedelta(days=1),
            book_id=book,
            trader_id=trader,
            **self._listed(exch),
        )

    def cds_index(
        self,
        desk_id: str,
        book_id: str | None = None,
        prefer_cpty: str | None = None,
        notional: float | None = None,
        direction: BuySell | None = None,
    ) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        book = book_id or book
        family = self.t.book_cds[book]
        _, _, _, spread_bp, _ = ref.CDS_INDICES[family]
        td = self._trade_date(300)
        if prefer_cpty:
            cid, ns = self._bilateral(book, prefer_cpty)
            cp = {"counterparty_id": cid, "clearing": ClearingType.BILATERAL, "netting_set_id": ns}
        else:
            cp = self._otc(book, "CCP_ICE", cleared_share=0.8)
        # BUY = buy protection.
        return Trade(
            trade_id=self._id("CDS"),
            instrument=inst.cds_index(family, self.bd),
            direction=direction or self._direction(),
            quantity=notional or self._round(self._lognormal(10e6, 300e6), 5e6),
            trade_price=round((spread_bp + float(self.rng.normal(0, spread_bp * 0.08))) / 1e4, 6),
            trade_date=td,
            settlement_date=td + relativedelta(days=1),
            book_id=book,
            trader_id=trader,
            **cp,
        )

    def commodity_future(
        self,
        desk_id: str,
        code: str | None = None,
        contracts: float | None = None,
        contract_index: int | None = None,
        book_id: str | None = None,
        direction: BuySell | None = None,
    ) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        book = book_id or book
        code = code or str(self.rng.choice(self.t.book_commodities[book]))
        exch, _, price, _, _, _ = ref.COMMODITIES[code]
        futs = inst.commodity_futures(code, self.bd)
        fut = futs[contract_index if contract_index is not None else int(self.rng.choice([0, 0, 1, 1, 2, 3]))]
        td = self._trade_date(90)
        return Trade(
            trade_id=self._id("CMF"),
            instrument=fut,
            direction=direction or self._direction(),
            quantity=contracts or self._round(self._lognormal(50, 1500), 10),
            trade_price=round(price * (1 + float(self.rng.normal(0, 0.03))), 3),
            trade_date=td,
            settlement_date=td,
            book_id=book,
            trader_id=trader,
            **self._listed(exch),
        )

    def crypto_spot(
        self,
        desk_id: str,
        symbol: str | None = None,
        units: float | None = None,
        direction: BuySell | None = None,
    ) -> Trade:
        book, trader = self._book_and_trader(desk_id)
        symbol = symbol or str(self.rng.choice(["BTC", "ETH"], p=[0.6, 0.4]))
        venue, price, _ = ref.CRYPTO[symbol]
        td = self._trade_date(200)
        lo, hi = (2, 150) if symbol == "BTC" else (50, 4000)
        return Trade(
            trade_id=self._id("CRY"),
            instrument=inst.crypto_spot(symbol),
            direction=direction or self._direction(),
            quantity=units or round(self._lognormal(lo, hi), 2),
            trade_price=round(price * (1 + float(self.rng.normal(0, 0.08))), 2),
            trade_date=td,
            settlement_date=td,
            book_id=book,
            trader_id=trader,
            **self._listed(venue),
        )

    # --- injected problems -------------------------------------------------------------
    def inject(self) -> tuple[list[Trade], list[Injection]]:
        trades: list[Trade] = []
        inj: list[Injection] = []

        # 1. USD 10Y DV01 concentration, all bilateral with Bank A (also cpty concentration).
        #    Struck 60bp above par: legacy off-market swaps novated in, so they carry positive
        #    PV and real counterparty exposure as well as DV01.
        t = []
        for _ in range(6):
            s = self._fair(
                self.swap(
                    "USD_RATES",
                    tenor="10Y",
                    notional=400e6,
                    side=SwapSide.RECEIVE_FIXED,
                    book_id="USD_MACRO_RV",
                    prefer_cpty="BANK_A",
                    max_days_back=10,
                )
            )
            ins = s.instrument.model_copy(update={"fixed_rate": round(s.instrument.fixed_rate + 0.006, 5)})
            t.append(s.model_copy(update={"instrument": ins, "trade_price": ins.fixed_rate}))
        trades += t
        inj.append(
            Injection(
                name="usd_10y_concentration",
                description="Six receive-fixed 10Y USD swaps of 400m each in USD Macro RV, "
                "all facing Bank A, "
                "struck 60bp above par (legacy novations).",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="USD Rates 10Y DV01 limit breach; Bank A the largest counterparty "
                "exposure.",
            )
        )

        # 2. Illiquid far-dated Brent.
        t = [
            self.commodity_future(
                "ENERGY",
                code="BRENT",
                contracts=10_000,
                contract_index=5,
                book_id="CRUDE",
                direction=BuySell.BUY,
            )
        ]
        trades += t
        inj.append(
            Injection(
                name="illiquid_brent",
                description="10,000 lots of the furthest Brent contract in one book.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Brent concentration limit; days-to-liquidate warning.",
            )
        )

        # 3. Outsized BTC exposure.
        t = [self.crypto_spot("DIGITAL", symbol="BTC", units=2500, direction=BuySell.BUY)]
        trades += t
        inj.append(
            Injection(
                name="btc_exposure",
                description="2,500 BTC long in Crypto Spot.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Digital assets stress-loss limit breach under BTC -50%.",
            )
        )

        # 4. Wrong-way risk: long USD/ARS forwards with an uncollateralised EM sovereign.
        t = [
            self.fx_forward(
                "EM_FX",
                pair="USD/ARS",
                notional=40e6,
                book_id="EM_FX_FWD",
                prefer_cpty="SOV_EM",
                direction=BuySell.BUY,
            )
            for _ in range(3)
        ]
        trades += t
        inj.append(
            Injection(
                name="wrong_way_sovereign",
                description="Three long USD/ARS forwards facing the Republic of Andoria, no CSA.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Wrong-way-risk flag: exposure rises as the counterparty's currency "
                "weakens.",
            )
        )

        # 5. Wrong-way risk: protection bought on HY from a HY-rated corporate.
        t = [
            self.cds_index(
                "HY_CREDIT", book_id="CDX_HY", prefer_cpty="CORP_AIR", notional=75e6, direction=BuySell.BUY
            )
        ]
        trades += t
        inj.append(
            Injection(
                name="wrong_way_credit",
                description="CDX HY protection bought from TransAtlantic Air (BB+, uncollateralised).",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Wrong-way-risk flag on counterparty watchlist.",
            )
        )

        # 6. Invalid trades for the data-quality module.
        base = self.swap("USD_RATES", book_id="USD_SWAPS_FLOW")
        bad = [
            base.model_copy(
                update={
                    "trade_id": self._id("IRS"),
                    "netting_set_id": None,
                    "clearing": ClearingType.BILATERAL,
                    "status": TradeStatus.INVALID,
                    "validation_errors": ("missing netting_set_id for bilateral OTC",),
                }
            ),
            base.model_copy(
                update={
                    "trade_id": self._id("IRS"),
                    "counterparty_id": "BANK_Z",
                    "clearing": ClearingType.BILATERAL,
                    "netting_set_id": "NS_BANK_Z_GMB_NY",
                }
            ),
            base.model_copy(update={"trade_id": self._id("IRS"), "book_id": "GHOST_BOOK"}),
        ]
        trades += bad
        inj.append(
            Injection(
                name="invalid_trades",
                description="One swap without a netting set, one facing an unknown counterparty, "
                "one booked to a non-existent book.",
                trade_ids=tuple(x.trade_id for x in bad),
                expected_detection="Data-quality exceptions; run trust verdict AMBER.",
            )
        )
        return trades, inj

    def inject_fund(self) -> tuple[list[Trade], list[Injection]]:
        """Planted problems for the fund template."""
        trades: list[Trade] = []
        inj: list[Injection] = []
        # 1. Crowded single-name long: NVDA at about 15% of NAV, sized off the live price.
        nav = self.t.nav or 2.0e9
        spot = ref.EQUITIES["NVDA"][4]
        if self.cfg.market_history is not None:
            try:
                spot = self.cfg.market_history.snapshot_at(self.bd).equity_spot("NVDA")
            except KeyError:
                pass
        shares = (
            self._round(0.15 * nav / spot / 4, 1000) / self.t.size_scale
        )  # _round is unscaled; undo scale
        shares = float(round(0.15 * nav / spot / 4 / 1000) * 1000)
        t = []
        for _ in range(4):
            x = self.cash_equity("EQ_LS_US")
            x = x.model_copy(
                update={
                    "instrument": inst.cash_equity("NVDA"),
                    "book_id": "US_TECH_LS",
                    "direction": BuySell.BUY,
                    "quantity": shares,
                    "counterparty_id": "EXCH_NYSE",
                    "clearing": ClearingType.EXCHANGE,
                }
            )
            t.append(x)
        trades += t
        inj.append(
            Injection(
                name="crowded_single_name",
                description=f"{4 * shares / 1e6:.1f}m NVDA shares long in US Tech L/S: about 15% of NAV in "
                "the most crowded name.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Single-name concentration limit; crowding flag; crowded exit horizon.",
            )
        )
        # 2. Prime-broker concentration: FX forwards all facing PB_GS.
        t = [
            self.fx_forward(
                "FX_CARRY",
                pair="USD/JPY",
                notional=60e6,
                book_id="G10_CARRY",
                prefer_cpty="PB_GS",
                direction=BuySell.BUY,
            )
            for _ in range(5)
        ]
        trades += t
        inj.append(
            Injection(
                name="pb_concentration",
                description="Five 60m USD/JPY forwards all facing PB_GS on top of the existing book there.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Prime-broker exposure and margin concentrated in one broker.",
            )
        )
        # 3. Illiquid position against monthly liquidity: far-dated natural gas.
        t = [
            self.commodity_future(
                "COMMODITY_TREND",
                code="NATGAS",
                contracts=2500,
                contract_index=5,
                book_id="ENERGY_TREND",
                direction=BuySell.BUY,
            )
        ]
        trades += t
        inj.append(
            Injection(
                name="illiquid_vs_redemptions",
                description="2,500 lots of the furthest natural gas contract in one book.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Days to liquidate beyond the monthly dealing window in the redemption "
                "stress.",
            )
        )
        # 4. Large short-vol book: sold index puts.
        t = []
        for _ in range(6):
            x = self.index_option("INDEX_VOL_ARB")
            t.append(x.model_copy(update={"direction": BuySell.SELL, "quantity": 400.0}))
        trades += t
        inj.append(
            Injection(
                name="short_vol",
                description="Six sold index options of 400 contracts each in Index Vol Arb.",
                trade_ids=tuple(x.trade_id for x in t),
                expected_detection="Negative gamma and vega concentration; stress loss on the equity crash "
                "scenario.",
            )
        )
        return trades, inj

    # --- driver ----------------------------------------------------------------------
    def run(self) -> GeneratedPortfolio:
        desks = list(self.t.desk_weight)
        w = np.array([self.t.desk_weight[d] for d in desks])
        w = w / w.sum()
        trades: list[Trade] = []
        counts = self.rng.multinomial(self.cfg.n_trades, w)
        for desk_id, k in zip(desks, counts, strict=True):
            mix = self.t.desk_mix[desk_id]
            recipes = list(mix)
            p = np.array([mix[r] for r in recipes])
            for r in self.rng.choice(recipes, size=int(k), p=p / p.sum()):
                trades.append(self._fair(getattr(self, str(r))(desk_id)))
        injections: list[Injection] = []
        if self.cfg.inject_problems:
            extra, injections = self.inject() if self.t.injector == "bank" else self.inject_fund()
            conc = {tid for i in injections if i.name == "usd_10y_concentration" for tid in i.trade_ids}
            trades += [t if t.trade_id in conc else self._fair(t) for t in extra]
        snap = PortfolioSnapshot(business_date=self.bd, trades=tuple(trades), source="SIM")
        return GeneratedPortfolio(snapshot=snap, injections=injections)


def generate_portfolio(
    org: Organisation, cp: CounterpartyUniverse, cfg: TradeGeneratorConfig
) -> GeneratedPortfolio:
    """Generate a reproducible simulated portfolio for the organisation."""
    return _Gen(org, cp, cfg).run()


def evolve_portfolio(
    previous: PortfolioSnapshot,
    new_date: date,
    org: Organisation,
    cp: CounterpartyUniverse,
    injections: list[Injection],
    market_history: MarketHistory | None = None,
    seed: int = 43,
    new_trade_share: float = 0.03,
    template: Template | None = None,
) -> tuple[PortfolioSnapshot, list[str]]:
    """Next-day portfolio: yesterday's trades plus a day of new business, with one of the USD
    10Y concentration swaps unwound (removed) so the concentration breach improves but persists.
    Returns the snapshot and a list of what changed, for the demo narrative."""
    gen = _Gen(org, cp, TradeGeneratorConfig(new_date, 0, seed, False, market_history, template))
    gen.n = max((int(t.trade_id.split("_")[-1]) for t in previous.trades), default=0)
    gen._trade_date = lambda *_a, **_k: new_date  # type: ignore[method-assign]  # all new business dated today
    changes: list[str] = []
    conc = next((i for i in injections if i.name == "usd_10y_concentration"), None)
    trades = list(previous.trades)
    if conc is not None:
        unwound = conc.trade_ids[0]
        trades = [t for t in trades if t.trade_id != unwound]
        changes.append(f"unwound {unwound} (one of the six 10Y Bank A swaps)")
    desks = list(gen.t.desk_weight)
    w = np.array([gen.t.desk_weight[d] for d in desks])
    n_new = max(int(len(previous) * new_trade_share), 1)
    counts = gen.rng.multinomial(n_new, w / w.sum())
    new: list[Trade] = []
    for desk_id, k in zip(desks, counts, strict=True):
        mix = gen.t.desk_mix[desk_id]
        recipes = list(mix)
        p = np.array([mix[r] for r in recipes])
        for r in gen.rng.choice(recipes, size=int(k), p=p / p.sum()):
            new.append(gen._fair(getattr(gen, str(r))(desk_id)))
    trades += new
    changes.append(f"{len(new)} new trades booked on {new_date}")
    return PortfolioSnapshot(business_date=new_date, trades=tuple(trades), source="SIM"), changes
