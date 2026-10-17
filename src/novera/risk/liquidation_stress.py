"""Concentration and liquidation stress (MR-025, decision round 53).

A stress scenario assumes the book can be exited at the end of its horizon at screen prices. A large
position cannot: it takes days to sell, the market keeps moving while it is sold, and selling it moves
the price. This record measures both, for every stress scenario of the run.

**Markets.** Trades that compete for the same volume form one market: a share and its ETF units are not
one market, but four trades in the same share are. The market key is the name for cash securities and
CDS, the underlying and contract month for futures, the underlying, expiry month and a 5% strike bucket
for options. An OTC trade (swaps, FX forwards, repo) is its own market: OTC is unwound dealer by dealer, not
through one order book. Offsetting trades in a market net: only the net is exited.

**Stressed exit horizon** of a market: H = net position / (participation × stressed volume share × ADV),
in days, with the MR-013 volume and the execution limits of the MR-023 default action rule.

**Liquidity-adjusted stress loss.** A scenario is a move over ``REFERENCE_HORIZON`` (10) days. A trade
whose market takes longer to exit stays exposed while it is sold, so its scenario P&L is scaled by
√(H / 10) where H > 10 (FRTB-style), at most √(120 / 10). Hedges in liquid markets do not scale: the
adjusted loss shows the basis between an illiquid position and its liquid hedge.

**Exit cost** (the gap between screen price and liquidation value), per market:

    cost = stressed half spread × net share + Y · σ_daily · √(Q / V_stressed)

where σ_daily is the market's daily stressed P&L volatility under the full stressed covariance of the
PCA space (MR-021, all components, so single-name risk counts) and Q / V_stressed its size in days of
stressed volume. The same cost computed trade by trade, as if each trade had the market to itself, is the
**trade view**; the difference is the **concentration add-on** (negative when offsetting trades net).

**Concentration groupings.** Issuer (bond issuer, equity ticker with its options, CDS reference entity),
option strike bucket and futures delivery month are reported from the markets. **Sector**: the names of
one equity or CDS sector are moved together beyond their market by the 99th-percentile 10-day sector
residual in the stored history (sector average less its regional index for equities, less the average of
all single names for CDS), in the direction that hurts, revalued in full and liquidity-adjusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

from novera.risk import scenario_covariance as sc_cov
from novera.simulation import reference_levels as ref

RECORD = "MR-025"
MODEL_VERSION = "1.0.0"
REFERENCE_HORIZON = 10.0
"""Days a stress scenario's move is taken to happen over (MR-018, MR-021)."""
MAX_HORIZON = 120.0
"""Longest exit horizon that scales a loss (FRTB's longest liquidity horizon)."""
STRIKE_BUCKET = 0.05
"""Width of an option strike bucket in log strike."""
SECTOR_QUANTILE = 0.99
TOP_POSITIONS = 10
INDEX_BY_CURRENCY = {"USD": "SPX", "EUR": "SX5E", "CHF": "SX5E", "GBP": "FTSE", "JPY": "NKY"}

MARKET_COLUMNS = [
    "market",
    "kind",
    "issuer",
    "sector",
    "desks",
    "trades",
    "net_exposure",
    "gross_exposure",
    "net_share",
    "horizon_days",
    "trade_horizon_days",
    "loss_scale",
    "daily_vol",
    "spread_cost",
    "impact_cost",
    "exit_cost",
    "trade_view_cost",
    "concentration_addon",
]
SCENARIO_COLUMNS = [
    "scenario_id",
    "name",
    "stress_set",
    "pnl",
    "adjusted_pnl",
    "horizon_addon",
    "exit_cost",
    "liquidation_loss",
    "scaled_trades",
]


# --- markets ---------------------------------------------------------------------------------------
def market_of(t: Any) -> tuple[str, str, str]:
    """(market key, kind, issuer) of a trade. Kinds: NAME, DELIVERY, STRIKE, OTC."""
    p = t.product_type.value
    ins = t.instrument
    if p in ("CASH_EQUITY", "ETF"):
        return f"{p}:{ins.ticker}", "NAME", ins.ticker
    if p == "MUTUAL_FUND":
        return f"{p}:{ins.fund_code}", "NAME", ""
    if p == "GOVERNMENT_BOND":
        return f"BOND:{ins.issuer}:{ins.maturity_date:%Y-%m}", "NAME", ins.issuer
    if p == "CDS_SINGLE_NAME":
        return f"CDS:{ins.reference_entity}", "NAME", ins.reference_entity
    if p == "CDS_INDEX":
        return f"CDX:{ins.index_family}:{ins.series}", "NAME", ""
    if p == "CRYPTO_SPOT":
        return f"CRYPTO:{ins.symbol}", "NAME", ""
    if p in ("EQUITY_INDEX_FUTURE", "INTEREST_RATE_FUTURE"):
        return f"FUT:{ins.index}:{ins.expiry_date:%Y-%m}", "DELIVERY", ""
    if p == "COMMODITY_FUTURE":
        return f"FUT:{ins.commodity}:{ins.expiry_date:%Y-%m}", "DELIVERY", ""
    if p in ("EQUITY_OPTION", "EQUITY_EXOTIC", "COMMODITY_OPTION", "FX_OPTION"):
        und = getattr(ins, "underlying", None) or getattr(ins, "commodity", None) or getattr(ins, "pair", "")
        bucket = round(math.log(ins.strike) / STRIKE_BUCKET)
        issuer = und if und in ref.EQUITIES else ""
        return f"OPT:{p}:{und}:{ins.expiry_date:%Y-%m}:K{bucket}", "STRIKE", issuer
    if p == "SWAPTION":
        return (
            f"OPT:SWAPTION:{t.currency}:{ins.expiry_date:%Y-%m}:K{round(ins.strike / 0.0025)}",
            "STRIKE",
            "",
        )
    # OTC rates and FX are unwound dealer by dealer, not through one order book: each trade is its own
    # market and the MR-013 volume is the capacity per unwind (decision 53.5).
    pair = getattr(ins, "pair", None)
    return f"OTC:{p}:{pair or t.currency}:{t.trade_id}", "OTC", ""


def sector_of(issuer: str) -> str:
    if issuer in ref.EQUITIES:
        return f"EQ:{ref.EQUITIES[issuer][2]}"
    if issuer in ref.CDS_SINGLE_NAMES:
        return f"CDS:{ref.CDS_SINGLE_NAMES[issuer][2]}"
    return ""


# --- covariance -----------------------------------------------------------------------------------
def daily_vol(space: Any, exposure: sparse.csr_matrix | np.ndarray) -> np.ndarray:
    """Daily stressed P&L volatility of each row of ``exposure`` (P&L per unit factor move, rows x
    factors in the space's order) under the full stressed covariance of the PCA space."""
    e = exposure.toarray() if sparse.issparse(exposure) else np.asarray(exposure)
    u = (e * space.sigma[None, :]) @ space.components
    u = u * np.sqrt(space.eigenvalues)[None, :]
    return np.sqrt((u * u).sum(axis=1) / space.horizon)


# --- the stage ------------------------------------------------------------------------------------
@dataclass
class LiquidationResult:
    markets: pd.DataFrame
    issuers: pd.DataFrame
    sectors: pd.DataFrame
    scenarios: pd.DataFrame
    desks: pd.DataFrame
    positions: pd.DataFrame
    parameters: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        m, s = self.markets, self.scenarios
        if m.empty:
            return {}
        longest = m.sort_values("horizon_days", ascending=False).iloc[0]
        out: dict[str, Any] = {
            "liquidation_exit_cost": float(m["exit_cost"].sum()),
            "liquidation_concentration_addon": float(m["concentration_addon"].sum()),
            "liquidation_longest_market": str(longest["market"]),
            "liquidation_longest_days": float(longest["horizon_days"]),
            "liquidation_markets_over_10d": int((m["horizon_days"] > REFERENCE_HORIZON).sum()),
        }
        if len(s):
            w = s.sort_values("liquidation_loss", kind="stable").iloc[0]
            h = s.sort_values("horizon_addon", kind="stable").iloc[0]
            out |= {
                "liquidation_worst_scenario": str(w["scenario_id"]),
                "liquidation_worst_loss": float(w["liquidation_loss"]),
                "liquidation_worst_plain": float(w["pnl"]),
                "liquidation_largest_horizon_addon_scenario": str(h["scenario_id"]),
                "liquidation_largest_horizon_addon": float(h["horizon_addon"]),
            }
        if len(self.sectors):
            w = self.sectors.sort_values("adjusted_pnl", kind="stable").iloc[0]
            out |= {
                "liquidation_worst_sector": str(w["sector"]),
                "liquidation_worst_sector_loss": float(w["adjusted_pnl"]),
            }
        return out


def run_liquidation_stress(
    pf: Any,
    book: Any,
    space: Any,
    history: Any,
    valuation: pd.DataFrame,
    liquidity_by_trade: pd.DataFrame,
    exposures: pd.Series,
    results: list[Any],
    limit_ids: set[str],
    participation: float,
    stressed_volume_share: float,
    stressed_spread_multiple: float,
    impact_coefficient: float,
) -> LiquidationResult:
    """Markets, exit horizons and costs, the liquidity-adjusted loss of every scenario, and the sector
    shocks. ``exposures``: signed delta-equivalent exposure per trade (for netting within a market)."""
    trades = list(book.trades)
    n = len(trades)
    v = valuation.drop_duplicates("trade_id").set_index("trade_id")
    liq = liquidity_by_trade.drop_duplicates("trade_id").set_index("trade_id")
    position = liq["position"].reindex(trades).fillna(0.0).to_numpy(dtype=float)
    adv = liq["adv"].reindex(trades).fillna(np.inf).to_numpy(dtype=float)
    half_spread = (
        liq["bidask_cost"].reindex(trades).fillna(0.0).to_numpy(dtype=float) * stressed_spread_multiple
    )
    desk = v["desk_id"].reindex(trades).fillna("(no desk)").astype(str).to_numpy()
    e = exposures.reindex(trades).fillna(0.0).to_numpy(dtype=float)
    keys, kinds, issuers = [], [], []
    for tid in trades:
        k, kind, iss = market_of(pf.by_id[tid])
        keys.append(k)
        kinds.append(kind)
        issuers.append(iss)
    mk = sorted(set(keys))
    mpos = {k: i for i, k in enumerate(mk)}
    mi = np.array([mpos[k] for k in keys])
    M = sparse.csr_matrix((np.ones(n), (mi, np.arange(n))), shape=(len(mk), n))  # noqa: N806

    # Factor exposure per trade (P&L per unit factor move), from the sensitivity rows.
    rows_per_factor = sparse.diags(1.0 / book.bump) @ book.A  # J x F
    G = (book.lin @ rows_per_factor).tocsr()  # noqa: N806 - trades x F
    sig_trade = daily_vol(space, G)
    sig_market = daily_vol(space, (M @ G).tocsr())

    # Netting: the share of a market's gross exposure (or position) that must be exited.
    gross_e = np.asarray(M @ np.abs(e)).ravel()
    net_e = np.asarray(M @ e).ravel()
    net_share = np.divide(np.abs(net_e), gross_e, out=np.ones_like(gross_e), where=gross_e > 0)
    stressed_adv = stressed_volume_share * adv
    q_days_trade = np.divide(
        position, stressed_adv, out=np.zeros(n), where=np.isfinite(stressed_adv) & (stressed_adv > 0)
    )
    # A market's size in days of stressed volume: its trades' positions add (they share the volume).
    q_days_market = np.asarray(M @ q_days_trade).ravel() * net_share
    horizon_m = q_days_market / participation
    horizon_t = q_days_trade / participation
    scale_m = np.sqrt(np.clip(horizon_m, REFERENCE_HORIZON, MAX_HORIZON) / REFERENCE_HORIZON)
    scale_t = scale_m[mi]

    spread_m = np.asarray(M @ half_spread).ravel() * net_share
    impact_m = impact_coefficient * sig_market * np.sqrt(q_days_market)
    exit_m = spread_m + impact_m
    trade_cost = half_spread + impact_coefficient * sig_trade * np.sqrt(q_days_trade)
    trade_view = np.asarray(M @ trade_cost).ravel()
    max_trade_h = np.zeros(len(mk))
    np.maximum.at(max_trade_h, mi, horizon_t)
    m_desks: dict[int, set[str]] = {}
    m_issuer: dict[int, str] = {}
    m_kind: dict[int, str] = {}
    for j in range(n):
        m_desks.setdefault(mi[j], set()).add(desk[j])
        m_issuer.setdefault(mi[j], issuers[j])
        m_kind.setdefault(mi[j], kinds[j])
    markets = pd.DataFrame(
        {
            "market": mk,
            "kind": [m_kind[i] for i in range(len(mk))],
            "issuer": [m_issuer[i] for i in range(len(mk))],
            "sector": [sector_of(m_issuer[i]) for i in range(len(mk))],
            "desks": [", ".join(sorted(m_desks[i])) for i in range(len(mk))],
            "trades": np.bincount(mi, minlength=len(mk)),
            "net_exposure": net_e,
            "gross_exposure": gross_e,
            "net_share": net_share,
            "horizon_days": horizon_m,
            "trade_horizon_days": max_trade_h,
            "loss_scale": scale_m,
            "daily_vol": sig_market,
            "spread_cost": spread_m,
            "impact_cost": impact_m,
            "exit_cost": exit_m,
            "trade_view_cost": trade_view,
            "concentration_addon": exit_m - trade_view,
        },
        columns=MARKET_COLUMNS,
    ).sort_values("exit_cost", ascending=False, kind="stable")

    # Issuers: every market of one name (cash, options, CDS, bonds).
    named = markets[markets["issuer"] != ""]
    issuers_df = (
        named.groupby("issuer", as_index=False)
        .agg(
            sector=("sector", "first"),
            markets=("market", "count"),
            gross_exposure=("gross_exposure", "sum"),
            net_exposure=("net_exposure", "sum"),
            longest_days=("horizon_days", "max"),
            exit_cost=("exit_cost", "sum"),
            concentration_addon=("concentration_addon", "sum"),
        )
        .sort_values("exit_cost", ascending=False, kind="stable")
    )
    total_gross = float(markets["gross_exposure"].sum()) or 1.0
    issuers_df["share_of_gross"] = issuers_df["gross_exposure"] / total_gross

    # Scenarios: plain and liquidity-adjusted loss, by desk, and the positions that add most.
    tpos = {t: i for i, t in enumerate(trades)}
    firm_exit = float(exit_m.sum())
    # A market's exit cost is shared among its trades in proportion to their own (trade-view) cost.
    tv = trade_view[mi]
    exit_by_trade = np.where(tv > 0, exit_m[mi] * trade_cost / np.where(tv > 0, tv, 1.0), 0.0)
    scen_rows, desk_rows, pos_rows = [], [], []
    for r in results:
        pnl = np.zeros(n)
        for tid, x in r.pnl.items():
            if tid in tpos:
                pnl[tpos[tid]] = float(x)
        adj = pnl * scale_t
        sid = r.scenario.scenario_id
        sset = "LIMIT" if sid in limit_ids else "INFORMATION"
        scen_rows.append(
            {
                "scenario_id": sid,
                "name": r.scenario.name,
                "stress_set": sset,
                "pnl": float(pnl.sum()),
                "adjusted_pnl": float(adj.sum()),
                "horizon_addon": float(adj.sum() - pnl.sum()),
                "exit_cost": firm_exit,
                "liquidation_loss": float(adj.sum()) - firm_exit,
                "scaled_trades": int(((scale_t > 1.0) & (pnl != 0)).sum()),
            }
        )
        dframe = pd.DataFrame({"desk_id": desk, "pnl": pnl, "adjusted_pnl": adj, "exit_cost": exit_by_trade})
        g = dframe.groupby("desk_id", as_index=False).sum()
        g.insert(0, "scenario_id", sid)
        g["horizon_addon"] = g["adjusted_pnl"] - g["pnl"]
        g["liquidation_loss"] = g["adjusted_pnl"] - g["exit_cost"]
        desk_rows.append(g)
        addon = adj - pnl
        for j in np.argsort(addon)[:TOP_POSITIONS]:
            if addon[j] >= 0:
                break
            pos_rows.append(
                {
                    "scenario_id": sid,
                    "trade_id": trades[j],
                    "desk_id": desk[j],
                    "market": keys[j],
                    "horizon_days": float(horizon_m[mi[j]]),
                    "loss_scale": float(scale_t[j]),
                    "pnl": float(pnl[j]),
                    "adjusted_pnl": float(adj[j]),
                    "horizon_addon": float(addon[j]),
                }
            )
    scenarios = pd.DataFrame(scen_rows, columns=SCENARIO_COLUMNS)
    desks = pd.concat(desk_rows, ignore_index=True) if desk_rows else pd.DataFrame()
    positions = pd.DataFrame(pos_rows)

    sectors = sector_shocks(pf, history, trades, issuers, scale_t)
    params = {
        "record": RECORD,
        "reference_horizon_days": REFERENCE_HORIZON,
        "max_horizon_days": MAX_HORIZON,
        "participation": participation,
        "stressed_volume_share": stressed_volume_share,
        "stressed_spread_multiple": stressed_spread_multiple,
        "impact_coefficient": impact_coefficient,
        "strike_bucket": STRIKE_BUCKET,
        "sector_quantile": SECTOR_QUANTILE,
        "markets": len(mk),
        "regime": space.regime,
    }
    return LiquidationResult(markets, issuers_df, sectors, scenarios, desks, positions, params)


def sector_shocks(
    pf: Any, history: Any, trades: list[str], issuers: list[str], scale: np.ndarray
) -> pd.DataFrame:
    """Each sector held through two or more names, moved together beyond its market by the 99th
    percentile 10-day residual of the stored history, in the direction that hurts."""
    try:
        ms = sc_cov.move_set(history, pf.as_of, int(REFERENCE_HORIZON), pf.universe)
    except ValueError:
        return pd.DataFrame()
    held: dict[str, set[str]] = {}
    for iss in issuers:
        s = sector_of(iss)
        if s:
            held.setdefault(s, set()).add(iss)
    cds_all = [f"CDS:{n}" for n in ref.CDS_SINGLE_NAMES if f"CDS:{n}" in ms.index]
    cds_market = np.nanmean(ms.moves[:, ms.cols(cds_all)], axis=1) if cds_all else None
    tpos = {t: i for i, t in enumerate(trades)}
    rows = []
    for sector, names in sorted(held.items()):
        if len(names) < 2:
            continue
        kind, label = sector.split(":", 1)
        members = sorted(n for n in names)
        if kind == "EQ":
            fids = [f"EQ:{n}" for n in members if f"EQ:{n}" in ms.index]
            resid = []
            for n in members:
                f, idx = f"EQ:{n}", INDEX_BY_CURRENCY.get(ref.EQUITIES[n][1])
                if f in ms.index and idx and f"EQIDX:{idx}" in ms.index:
                    resid.append(ms.moves[:, ms.cols([f])[0]] - ms.moves[:, ms.cols([f"EQIDX:{idx}"])[0]])
        else:
            fids = [f"CDS:{n}" for n in members if f"CDS:{n}" in ms.index]
            resid = (
                [ms.moves[:, ms.cols([f])[0]] - cds_market for f in fids] if cds_market is not None else []
            )
        if len(resid) < 2:
            continue
        avg = np.nanmean(np.vstack(resid), axis=0)
        avg = avg[np.isfinite(avg)]
        if not len(avg):
            continue
        up, down = float(np.quantile(avg, SECTOR_QUANTILE)), float(np.quantile(avg, 1 - SECTOR_QUANTILE))
        best: dict[str, Any] | None = None
        for direction, size in (("UP", up), ("DOWN", down)):
            shocks = {f: size for f in fids}
            pnl = pf.pnl_under_shocks(shocks)
            total = float(sum(pnl.values()))
            adj = float(sum(x * scale[tpos[t]] for t, x in pnl.items() if t in tpos))
            row = {
                "sector": label,
                "asset": "equity" if kind == "EQ" else "credit",
                "names": len(fids),
                "direction": direction,
                "shock": size,
                "pnl": total,
                "adjusted_pnl": adj,
                "trades": int(sum(1 for x in pnl.values() if x != 0)),
            }
            if best is None or adj < best["adjusted_pnl"]:
                best = row
        if best is not None:
            rows.append(best)
    out = pd.DataFrame(rows)
    return out.sort_values("adjusted_pnl", kind="stable") if len(out) else out
