"""Market-data simulator: risk-factor universe, correlated daily history, snapshots.

Model (methodology record SIM-001):
- One global risk-appetite factor drives cross-asset correlation. Each factor group loads
  on it with a sign that matches market experience: equities, crypto, energy and yields
  rise in risk-on; credit spreads, implied vols, gold and the US dollar fall.
- Prices, spreads and vols follow lognormal daily steps; zero rates follow additive steps
  with level, slope and curvature components and mild mean reversion to base levels.
- Vol surfaces: the ATM term structure evolves (negatively correlated with the underlying)
  and a static smile shape is applied around it. Skew steepens during the crash episode.
- Two stylised episodes are overlaid as drifts and vol multipliers over fixed windows:
  a risk-off crash and a rates shock. They are explicitly synthetic.
- The business-date snapshot plants two data-quality problems: a stale EUR/USD vol surface
  and a missing USD 7Y node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from novera.market_data.risk_factors import (
    TENOR_YEARS,
    RiskFactor,
    RiskFactorType,
    cds_id,
    cmd_id,
    crypto_id,
    eq_id,
    eqidx_id,
    fx_id,
    ir_id,
    swvol_id,
    vol_id,
)
from novera.market_data.snapshot import MarketSnapshot
from novera.simulation import reference_levels as ref

MODEL_VERSION = "1.0.0"


# --- Universe ------------------------------------------------------------------------


def build_risk_factor_universe() -> list[RiskFactor]:
    out: list[RiskFactor] = []
    for ccy, curve in ref.SWAP_CURVES.items():
        for tenor in curve:
            out.append(
                RiskFactor(
                    factor_id=ir_id(ccy, tenor),
                    factor_type=RiskFactorType.IR_ZERO,
                    asset_class="RATES",
                    currency=ccy,
                    underlying=ccy,
                    tenor=tenor,
                    tenor_years=TENOR_YEARS[tenor],
                    unit="rate",
                    shock_type="ABSOLUTE",
                )
            )
    for pair in ref.FX_SPOT:
        out.append(
            RiskFactor(
                factor_id=fx_id(pair),
                factor_type=RiskFactorType.FX_SPOT,
                asset_class="FX",
                currency=pair[4:],
                underlying=pair,
                unit="price",
                shock_type="RELATIVE",
            )
        )
    for ticker, (_, ccy, _, _, _, _) in ref.EQUITIES.items():
        out.append(
            RiskFactor(
                factor_id=eq_id(ticker),
                factor_type=RiskFactorType.EQUITY_SPOT,
                asset_class="EQUITY",
                currency=ccy,
                underlying=ticker,
                unit="price",
                shock_type="RELATIVE",
            )
        )
    for index, (_, ccy, _, _, _) in ref.EQUITY_INDICES.items():
        out.append(
            RiskFactor(
                factor_id=eqidx_id(index),
                factor_type=RiskFactorType.EQUITY_INDEX,
                asset_class="EQUITY",
                currency=ccy,
                underlying=index,
                unit="level",
                shock_type="RELATIVE",
            )
        )
    for code in ref.COMMODITIES:
        for tenor in ref.COMMODITY_TENORS:
            out.append(
                RiskFactor(
                    factor_id=cmd_id(code, tenor),
                    factor_type=RiskFactorType.COMMODITY_CURVE,
                    asset_class="COMMODITY",
                    currency="USD",
                    underlying=code,
                    tenor=tenor,
                    tenor_years=TENOR_YEARS[tenor],
                    unit="price",
                    shock_type="RELATIVE",
                )
            )
    for family, (ccy, _, _, _, _) in ref.CDS_INDICES.items():
        out.append(
            RiskFactor(
                factor_id=cds_id(family),
                factor_type=RiskFactorType.CREDIT_SPREAD,
                asset_class="CREDIT",
                currency=ccy,
                underlying=family,
                unit="bp",
                shock_type="ABSOLUTE",
            )
        )
    for entity, (ccy, _, _, _, _, _) in ref.CDS_SINGLE_NAMES.items():
        out.append(
            RiskFactor(
                factor_id=cds_id(entity),
                factor_type=RiskFactorType.CREDIT_SPREAD,
                asset_class="CREDIT",
                currency=ccy,
                underlying=entity,
                unit="bp",
                shock_type="ABSOLUTE",
            )
        )
    for ccy in ref.SWAPTION_CURRENCIES:
        for expiry in ref.SWAPTION_EXPIRIES:
            for tenor in ref.SWAPTION_TENORS:
                out.append(
                    RiskFactor(
                        factor_id=swvol_id(ccy, expiry, tenor),
                        factor_type=RiskFactorType.SWAPTION_VOL,
                        asset_class="RATES",
                        currency=ccy,
                        underlying=ccy,
                        tenor=tenor,
                        tenor_years=TENOR_YEARS[tenor],
                        expiry_years=TENOR_YEARS[expiry],
                        unit="bp",
                        shock_type="RELATIVE",
                    )
                )
    for symbol in ref.CRYPTO:
        out.append(
            RiskFactor(
                factor_id=crypto_id(symbol),
                factor_type=RiskFactorType.CRYPTO_SPOT,
                asset_class="DIGITAL_ASSET",
                currency="USD",
                underlying=symbol,
                unit="price",
                shock_type="RELATIVE",
            )
        )
    for underlying, ccy, ac in _vol_underlyings():
        for expiry in ref.VOL_EXPIRIES:
            for m in ref.VOL_MONEYNESS:
                out.append(
                    RiskFactor(
                        factor_id=vol_id(underlying, expiry, m),
                        factor_type=RiskFactorType.IMPLIED_VOL,
                        asset_class=ac,
                        currency=ccy,
                        underlying=underlying,
                        tenor=expiry,
                        expiry_years=TENOR_YEARS[expiry],
                        moneyness=m,
                        unit="vol",
                        shock_type="RELATIVE",
                    )
                )
    return out


def _vol_underlyings() -> list[tuple[str, str, str]]:
    out = [(t, v[1], "EQUITY") for t, v in ref.EQUITIES.items()]
    out += [(i, v[1], "EQUITY") for i, v in ref.EQUITY_INDICES.items()]
    out += [(p, p[4:], "FX") for p in ref.FX_SPOT]
    out += [(c, "USD", "COMMODITY") for c in ref.COMMODITY_VOL_CODES]
    return out


# --- Episodes ------------------------------------------------------------------------


@dataclass(frozen=True)
class Episode:
    """A stylised stress window overlaid on the history. Moves are totals over the window."""

    name: str
    start_offset_days: int  # business days before the end of history
    length: int
    equity: float
    credit_mult: float  # multiplicative on spreads
    vol_mult: float
    crypto: float
    usd: float  # USD strength (positive = dollar up)
    rates_short_bp: float
    rates_long_bp: float
    energy: float
    gold: float
    skew_mult: float = 1.0
    daily_vol_mult: float = 2.0


DEFAULT_EPISODES: tuple[Episode, ...] = (
    Episode(
        "stylised_risk_off_crash",
        start_offset_days=480,
        length=22,
        equity=-0.27,
        credit_mult=2.1,
        vol_mult=2.3,
        crypto=-0.45,
        usd=0.06,
        rates_short_bp=-90,
        rates_long_bp=-60,
        energy=-0.32,
        gold=0.05,
        skew_mult=1.6,
        daily_vol_mult=2.5,
    ),
    Episode(
        "stylised_rates_shock",
        start_offset_days=220,
        length=30,
        equity=-0.13,
        credit_mult=1.35,
        vol_mult=1.45,
        crypto=-0.25,
        usd=0.04,
        rates_short_bp=150,
        rates_long_bp=90,
        energy=0.08,
        gold=-0.06,
        skew_mult=1.2,
        daily_vol_mult=1.8,
    ),
)


@dataclass(frozen=True)
class MarketSimConfig:
    end_date: date
    years: float = 3.0
    seed: int = 42
    episodes: tuple[Episode, ...] = DEFAULT_EPISODES
    plant_data_quality_problems: bool = True
    problem_date: date | None = None
    """Business date that carries the planted data-quality problems (default: end_date)."""
    snapshot_days: int = 2
    """How many trailing daily snapshots to return in ``snapshots`` (at least 2)."""


@dataclass
class GeneratedMarketData:
    universe: list[RiskFactor]
    history: pd.DataFrame  # columns as_of, factor_id, value
    snapshot: MarketSnapshot  # the problem date, with planted problems
    previous_snapshot: MarketSnapshot  # the day before the problem date, clean
    snapshots: dict[date, MarketSnapshot] = field(default_factory=dict)  # trailing days, oldest first
    planted: list[str] = field(default_factory=list)


# --- Simulator -----------------------------------------------------------------------


def business_days(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def business_days_after(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


class _Sim:
    def __init__(self, cfg: MarketSimConfig) -> None:
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.n = int(round(cfg.years * 261))
        self.dates = business_days(cfg.end_date, self.n)
        self.dt = 1 / 261
        self.sq = np.sqrt(self.dt)
        self.g = self.rng.standard_normal(self.n)  # global risk appetite
        self.vol_mult = np.ones(self.n)  # daily vol scaling by episodes
        self.skew_mult = np.ones(self.n)
        self.drift: dict[str, np.ndarray] = {
            k: np.zeros(self.n)
            for k in (
                "equity",
                "credit",
                "vol",
                "crypto",
                "usd",
                "rates_short",
                "rates_long",
                "energy",
                "gold",
            )
        }
        for ep in cfg.episodes:
            s = max(self.n - ep.start_offset_days, 0)
            e = min(s + ep.length, self.n)
            n_days = e - s
            if n_days <= 0:
                continue
            sl = slice(s, e)
            self.vol_mult[sl] = ep.daily_vol_mult
            self.skew_mult[sl] = ep.skew_mult
            self.drift["equity"][sl] += np.log1p(ep.equity) / n_days
            self.drift["credit"][sl] += np.log(ep.credit_mult) / n_days
            self.drift["vol"][sl] += np.log(ep.vol_mult) / n_days
            self.drift["crypto"][sl] += np.log1p(ep.crypto) / n_days
            self.drift["usd"][sl] += np.log1p(ep.usd) / n_days
            self.drift["rates_short"][sl] += ep.rates_short_bp / 1e4 / n_days
            self.drift["rates_long"][sl] += ep.rates_long_bp / 1e4 / n_days
            self.drift["energy"][sl] += np.log1p(ep.energy) / n_days
            self.drift["gold"][sl] += np.log1p(ep.gold) / n_days
            # After the window, vols and spreads decay back over ~60 days.
            tail = slice(e, min(e + 60, self.n))
            tl = min(e + 60, self.n) - e
            if tl > 0:
                self.drift["vol"][tail] -= np.log(ep.vol_mult) * 0.8 / tl
                self.drift["credit"][tail] -= np.log(ep.credit_mult) * 0.6 / tl
        self.series: dict[str, np.ndarray] = {}

    def _noise(self, k: int = 1) -> np.ndarray:
        z = self.rng.standard_normal((self.n, k)) if k > 1 else self.rng.standard_normal(self.n)
        return z

    def _lognormal_path(
        self,
        s0: float,
        ann_vol: float,
        beta: float,
        extra_drift: np.ndarray,
        common: np.ndarray | None = None,
        common_w: float = 0.0,
        annual_drift: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        eps = self._noise()
        z = beta * self.g + common_w * (common if common is not None else 0.0)
        z = z + np.sqrt(max(1 - beta**2 - common_w**2, 0.05)) * eps
        r = ann_vol * self.sq * self.vol_mult * z + annual_drift * self.dt + extra_drift
        path = s0 * np.exp(np.concatenate([[0.0], np.cumsum(r[1:])]))
        return path, r

    # --- groups ------------------------------------------------------------------------
    def rates(self) -> None:
        loads = {t: TENOR_YEARS[t] for t in ref.CURVE_TENORS}
        tenors = list(ref.CURVE_TENORS)
        ty = np.array([loads[t] for t in tenors])
        slope_load = (np.log(ty) - np.log(ty).mean()) / (np.log(ty).max() - np.log(ty).min()) * 2  # -1..1
        curv_load = 1 - 2 * np.abs(slope_load)
        for ccy, base_curve in ref.SWAP_CURVES.items():
            base = np.array([base_curve[t] for t in tenors])
            dv = ref.RATES_DAILY_VOL[ccy]
            e_level, e_slope, e_curv = self._noise(), self._noise(), self._noise()
            level = 0.35 * self.g + np.sqrt(1 - 0.35**2) * e_level
            path = np.zeros((self.n, len(tenors)))
            cur = base.copy()
            for i in range(self.n):
                short = self.drift["rates_short"][i]
                long_ = self.drift["rates_long"][i]
                ep_drift = short + (long_ - short) * (slope_load + 1) / 2
                shock = (
                    dv
                    * self.vol_mult[i]
                    * (level[i] + 0.45 * slope_load * e_slope[i] + 0.25 * curv_load * e_curv[i])
                )
                cur = cur + shock + ep_drift - 0.003 * (cur - base)
                cur = np.maximum(cur, -0.01)
                path[i] = cur
            for j, t in enumerate(tenors):
                self.series[ir_id(ccy, t)] = path[:, j]

    def fx(self) -> None:
        usd = self._noise()
        usd_z = -0.45 * self.g + np.sqrt(1 - 0.45**2) * usd  # dollar strength
        self.usd_z = usd_z
        self.fx_returns: dict[str, np.ndarray] = {}
        usd_pairs = {p: v for p, v in ref.FX_SPOT.items() if p != "EUR/GBP"}
        for pair, (spot, vol) in usd_pairs.items():
            base, quote = pair[:3], pair[4:]
            g10 = {"EUR", "GBP", "JPY", "AUD", "CHF", "CAD", "NZD", "USD", "SGD"}
            em = base not in g10 or quote not in g10
            sign = 1.0 if base == "USD" else -1.0  # USD strength raises USD/xxx, lowers xxx/USD
            w = 0.6 if em else 0.75
            eps = self._noise()
            z = sign * w * usd_z + np.sqrt(1 - w**2) * eps
            drift = sign * self.drift["usd"] * (1.5 if em else 1.0)
            r = vol * self.sq * self.vol_mult * z + drift
            path = spot * np.exp(np.concatenate([[0.0], np.cumsum(r[1:])]))
            self.series[fx_id(pair)] = path
            self.fx_returns[pair] = r
        self.series[fx_id("EUR/GBP")] = self.series[fx_id("EUR/USD")] / self.series[fx_id("GBP/USD")]
        self.fx_returns["EUR/GBP"] = self.fx_returns["EUR/USD"] - self.fx_returns["GBP/USD"]

    def equities(self) -> None:
        self.eq_returns: dict[str, np.ndarray] = {}
        region = {"US": self._noise(), "EU": self._noise()}
        for index, (_, _, level, vol, _) in ref.EQUITY_INDICES.items():
            reg = "US" if index in ("SPX", "NDX") else "EU"
            path, r = self._lognormal_path(level, vol, 0.80, self.drift["equity"], region[reg], 0.4, 0.08)
            self.series[eqidx_id(index)] = path
            self.eq_returns[index] = r
        for ticker, (_, _, _, country, price, vol) in ref.EQUITIES.items():
            reg = "US" if country == "US" else "EU"
            path, r = self._lognormal_path(price, vol, 0.55, self.drift["equity"], region[reg], 0.35, 0.08)
            self.series[eq_id(ticker)] = path
            self.eq_returns[ticker] = r

    def commodities(self) -> None:
        self.cmd_returns: dict[str, np.ndarray] = {}
        energy_f, metal_f = self._noise(), self._noise()
        for code, (_, _, price, vol, _, _) in ref.COMMODITIES.items():
            if code in ("BRENT", "WTI", "NATGAS"):
                beta, common, w, drift = 0.35, energy_f, 0.6, self.drift["energy"]
            elif code in ("GOLD", "SILVER"):
                beta, common, w, drift = -0.15, metal_f, 0.5, self.drift["gold"]
            else:
                beta, common, w, drift = 0.45, metal_f, 0.4, self.drift["energy"] * 0.3
            spot, r = self._lognormal_path(price, vol, beta, drift, common, w)
            self.cmd_returns[code] = r
            slope = ref.COMMODITY_SLOPE[code]
            tilt = np.cumsum(self.rng.normal(0, 0.002, self.n))
            tilt -= 0.02 * np.cumsum(tilt) / np.arange(1, self.n + 1)  # keep it bounded-ish
            for tenor in ref.COMMODITY_TENORS:
                t = TENOR_YEARS[tenor]
                self.series[cmd_id(code, tenor)] = spot * (1 + (slope + tilt) * t)

    def credit(self, single_names: bool = False) -> None:
        """Index spreads, or (second pass, drawn after every Phase 1 group so their random
        streams are unchanged) single names: wider spreads, higher vol and a weaker loading on
        the global factor so index hedges leave idiosyncratic basis."""
        if single_names:
            names = {e: (v[3], 0.45 if v[5] == "IG" else 0.55, 0.45) for e, v in ref.CDS_SINGLE_NAMES.items()}
        else:
            names = {
                f: (v[3], 0.38 if "IG" in f or "MAIN" in f else 0.32, 0.65)
                for f, v in ref.CDS_INDICES.items()
            }
        for family, (spread_bp, vol, load) in names.items():
            eps = self._noise()
            z = -load * self.g + np.sqrt(1 - load**2) * eps
            log_s = np.log(spread_bp)
            path = np.zeros(self.n)
            cur = log_s
            for i in range(self.n):
                cur += (
                    vol * self.sq * self.vol_mult[i] * z[i] + self.drift["credit"][i] - 0.004 * (cur - log_s)
                )
                path[i] = cur
            self.series[cds_id(family)] = np.exp(path)

    def crypto(self) -> None:
        cf = self._noise()
        for symbol, (_, price, vol) in ref.CRYPTO.items():
            # Positive drift offsets the two crash episodes so BTC ends near its reference level.
            path, _ = self._lognormal_path(price, vol, 0.35, self.drift["crypto"], cf, 0.75, 0.25)
            self.series[crypto_id(symbol)] = path

    def swaption_vols(self) -> None:
        """Normal vol cube: one log-vol path per currency, correlated with the rates level
        shock, with a hump at 1Y expiry and a decline in tenor."""
        exp_shape = {"3M": 1.05, "1Y": 1.10, "2Y": 1.04, "5Y": 0.95}
        ten_shape = {"2Y": 1.08, "5Y": 1.00, "10Y": 0.94, "30Y": 0.85}
        for ccy in ref.SWAPTION_CURRENCIES:
            base = ref.SWAPTION_NORMAL_VOL_BP[ccy]
            move = np.abs(np.diff(self.series[ir_id(ccy, "5Y")], prepend=self.series[ir_id(ccy, "5Y")][0]))
            std = move / (np.std(move) + 1e-12)
            eps = self._noise()
            z = 0.35 * (std - std.mean()) + np.sqrt(1 - 0.35**2) * eps
            log_v = np.log(base)
            cur = log_v
            atm = np.zeros(self.n)
            for i in range(self.n):
                cur += 0.5 * self.sq * z[i] + self.drift["vol"][i] * 0.6 - 0.01 * (cur - log_v)
                atm[i] = cur
            atm = np.exp(atm)
            for e in ref.SWAPTION_EXPIRIES:
                for t in ref.SWAPTION_TENORS:
                    self.series[swvol_id(ccy, e, t)] = np.clip(atm * exp_shape[e] * ten_shape[t], 20.0, 400.0)

    def vols(self) -> None:
        expiries = np.array([TENOR_YEARS[e] for e in ref.VOL_EXPIRIES])
        mny = np.array(ref.VOL_MONEYNESS)
        for underlying, _, ac in _vol_underlyings():
            if ac == "COMMODITY":
                base_vol = ref.COMMODITIES[underlying][3]
                ret = self.cmd_returns[underlying]
                # Energy vol rises when prices rise (supply shocks); metals behave like equities.
                energy = underlying in ("BRENT", "WTI", "NATGAS")
                vov, corr, skew, smile = 0.55, (0.25 if energy else -0.35), (-0.10 if energy else 0.15), 0.12
                term = np.array([1.08, 1.03, 1.0, 0.97, 0.95])
            elif ac == "EQUITY":
                base_vol = (
                    ref.EQUITY_INDICES[underlying][3]
                    if underlying in ref.EQUITY_INDICES
                    else ref.EQUITIES[underlying][5]
                )
                ret = self.eq_returns[underlying]
                vov, corr, skew, smile = 0.9, -0.65, 0.30, 0.10
                term = np.array([1.06, 1.02, 1.0, 0.98, 0.97])
            else:
                base_vol = ref.FX_SPOT[underlying][1]
                ret = self.fx_returns[underlying]
                vov, corr, skew, smile = 0.55, -0.15 if underlying.startswith("USD") else 0.15, 0.03, 0.12
                term = np.array([1.03, 1.01, 1.0, 1.0, 1.0])
            # ATM log-vol path, correlated with the underlying's standardised return.
            std = ret / (np.std(ret) + 1e-12)
            eps = self._noise()
            z = corr * std + np.sqrt(1 - corr**2) * eps
            log_v = np.log(base_vol)
            atm = np.zeros(self.n)
            cur = log_v
            for i in range(self.n):
                cur += vov * self.sq * z[i] + self.drift["vol"][i] - 0.01 * (cur - log_v)
                atm[i] = cur
            atm = np.exp(atm)
            for ei, expiry in enumerate(ref.VOL_EXPIRIES):
                t = expiries[ei]
                for m in mny:
                    # Log-moneyness normalised by sqrt(T) and capped near the 10-delta wings.
                    lm = float(np.clip(np.log(m) / np.sqrt(max(t, 1 / 12)), -1.0, 1.0)) * 1.5
                    shape = np.clip(1 - skew * self.skew_mult * lm + smile * lm**2, 0.6, 2.0)
                    node = np.clip(atm * term[ei] * shape, 0.03, 2.5)
                    self.series[vol_id(underlying, expiry, float(m))] = node

    def run(self) -> GeneratedMarketData:
        self.rates()
        self.fx()
        self.equities()
        self.commodities()
        self.credit()
        self.crypto()
        self.vols()
        # Phase 6 families are drawn last so the Phase 1 history is reproduced exactly.
        self.credit(single_names=True)
        self.swaption_vols()
        universe = build_risk_factor_universe()
        ids = [f.factor_id for f in universe]
        missing = [i for i in ids if i not in self.series]
        assert not missing, missing[:5]
        matrix = np.column_stack([self.series[i] for i in ids])
        history = pd.DataFrame(
            {
                "as_of": np.repeat(np.array(self.dates, dtype="datetime64[D]"), len(ids)),
                "factor_id": np.tile(np.array(ids), self.n),
                "value": matrix.ravel(),
            }
        )
        problem_date = self.cfg.problem_date or self.dates[-1]
        if problem_date not in self.dates:
            raise ValueError(f"problem_date {problem_date} is not a business day in the history")
        pi = self.dates.index(problem_date)
        if pi < 1:
            raise ValueError("problem_date must have at least one prior day of history")
        n_snap = max(self.cfg.snapshot_days, 2)
        first = max(min(pi - 1, self.n - n_snap), 0)
        snapshots: dict[date, MarketSnapshot] = {}
        planted: list[str] = []
        for k in range(first, self.n):
            vals = {i: float(matrix[k, j]) for j, i in enumerate(ids)}
            observed: dict[str, date] = {}
            if k == pi and self.cfg.plant_data_quality_problems:
                prev_vals = {i: float(matrix[k - 1, j]) for j, i in enumerate(ids)}
                for i in [x for x in ids if x.startswith("VOL:EURUSD:")]:
                    vals[i] = prev_vals[i]
                    observed[i] = self.dates[k - 1]
                planted.append(
                    "stale_eurusd_vol_surface: EUR/USD vol surface not updated on the business date"
                )
                del vals[ir_id("USD", "7Y")]
                planted.append("missing_usd_7y_node: USD zero curve is missing its 7Y node")
            snapshots[self.dates[k]] = MarketSnapshot(as_of=self.dates[k], values=vals, observed_at=observed)
        snapshot = snapshots[problem_date]
        previous = snapshots[self.dates[pi - 1]]
        return GeneratedMarketData(universe, history, snapshot, previous, snapshots, planted)


def generate_market_data(cfg: MarketSimConfig) -> GeneratedMarketData:
    """Generate the risk-factor universe, daily history and business-date snapshots."""
    return _Sim(cfg).run()
