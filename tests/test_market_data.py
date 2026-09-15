from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.market_data import MarketSnapshot, ZeroCurve
from novera.simulation.market_data import (
    MarketSimConfig,
    build_risk_factor_universe,
    generate_market_data,
)
from novera.storage.duckdb_repository import DuckDBRepository

BD = date(2026, 9, 11)


@pytest.fixture(scope="module")
def md():
    return generate_market_data(MarketSimConfig(end_date=BD, years=1.0, seed=11))


def test_universe_shape() -> None:
    u = build_risk_factor_universe()
    ids = [f.factor_id for f in u]
    assert len(ids) == len(set(ids))
    types = {f.factor_type.value for f in u}
    assert types == {
        "IR_ZERO",
        "FX_SPOT",
        "EQUITY_SPOT",
        "EQUITY_INDEX",
        "COMMODITY_CURVE",
        "CREDIT_SPREAD",
        "CRYPTO_SPOT",
        "IMPLIED_VOL",
        "SWAPTION_VOL",
    }
    assert "IR:USD:10Y" in ids and "VOL:SPX:3M:0.90" in ids and "CMD:BRENT:3Y" in ids


def test_history_is_reproducible_and_clean(md) -> None:
    again = generate_market_data(MarketSimConfig(end_date=BD, years=1.0, seed=11))
    assert again.snapshot.snapshot_id == md.snapshot.snapshot_id
    h = md.history
    assert h["as_of"].nunique() == 261
    assert h["as_of"].max().date() == BD
    assert not h["value"].isna().any()
    prices = h[~h["factor_id"].str.startswith("IR:")]
    assert (prices["value"] > 0).all()
    vols = h[h["factor_id"].str.startswith("VOL:")]
    assert vols["value"].between(0.02, 3.0).all()


def test_zero_curve_interpolation() -> None:
    c = ZeroCurve("USD", np.array([1.0, 2.0, 5.0]), np.array([0.03, 0.035, 0.04]))
    assert c.df(0) == 1.0
    assert 0 < c.df(5.0) < c.df(2.0) < c.df(1.0) < 1
    assert c.zero(0.5) == pytest.approx(0.03)  # flat extrapolation
    assert c.zero(10.0) == pytest.approx(0.04)
    assert c.forward(1.0, 2.0) > c.zero(1.0)  # upward sloping curve
    with pytest.raises(ValueError):
        ZeroCurve("USD", np.array([2.0, 1.0]), np.array([0.03, 0.03]))


def test_snapshot_accessors(md) -> None:
    s = md.previous_snapshot
    usd = s.zero_curve("USD")
    assert len(usd.tenors) == 12
    assert s.fx_spot("EUR/USD") == pytest.approx(1 / s.fx_spot("USD/EUR"))
    assert s.fx_spot("EUR/JPY") == pytest.approx(s.fx_spot("EUR/USD") * s.fx_spot("USD/JPY"))
    assert s.fx_spot("USD/USD") == 1.0
    surf = s.vol_surface("SPX")
    assert surf.vol(0.25, 0.9) > surf.vol(0.25, 1.0) > surf.vol(0.25, 1.1), "equity skew"
    fx = s.vol_surface("EUR/USD")
    assert abs(fx.vol(1.0, 0.9) - fx.vol(1.0, 1.1)) < 0.03, "FX smile roughly symmetric"
    brent = s.commodity_curve("BRENT")
    assert brent.price(0.25) > brent.price(2.0), "Brent in backwardation"
    assert s.cds_spread_bp("CDX.NA.HY") > s.cds_spread_bp("CDX.NA.IG")


def test_planted_data_quality_problems(md) -> None:
    s = md.snapshot
    assert not s.has("IR:USD:7Y") and md.previous_snapshot.has("IR:USD:7Y")
    stale = s.stale_factors()
    assert stale and all(k.startswith("VOL:EURUSD:") for k in stale)
    assert len(s.zero_curve("USD").tenors) == 11
    clean = generate_market_data(
        MarketSimConfig(end_date=BD, years=0.3, seed=1, plant_data_quality_problems=False)
    )
    assert clean.snapshot.stale_factors() == [] and clean.snapshot.has("IR:USD:7Y")


def test_crisis_episodes_visible() -> None:
    md3 = generate_market_data(MarketSimConfig(end_date=BD, seed=42))  # the shipped five-year default
    spx = md3.history[md3.history["factor_id"] == "EQIDX:SPX"].set_index("as_of")["value"]
    peak_to_trough = (spx / spx.cummax() - 1).min()
    assert peak_to_trough < -0.20, f"crash episode should show a >20% drawdown, got {peak_to_trough:.1%}"
    usd10 = md3.history[md3.history["factor_id"] == "IR:USD:10Y"].set_index("as_of")["value"]
    assert usd10.diff(30).max() > 0.006, "rates shock should show a >60bp 30-day rise"
    vol = md3.history[md3.history["factor_id"] == "VOL:SPX:1M:1.00"].set_index("as_of")["value"]
    assert vol.max() / vol.median() > 1.6


def test_market_data_round_trip(tmp_path, md) -> None:
    with DuckDBRepository(tmp_path / "md.duckdb") as repo:
        repo.init_schema()
        repo.save_risk_factors(md.universe)
        assert len(repo.load_risk_factors()) == len(md.universe)
        n = repo.save_market_history(md.history)
        assert n == len(md.history)
        sub = repo.load_market_history(["EQIDX:SPX", "IR:USD:10Y"], start=date(2026, 9, 1))
        assert set(sub["factor_id"]) == {"EQIDX:SPX", "IR:USD:10Y"} and len(sub) == 2 * 9
        sid = repo.save_market_snapshot(md.snapshot)
        assert repo.save_market_snapshot(md.snapshot) == sid
        loaded = repo.load_market_snapshot(sid)
        assert loaded.snapshot_id == sid and loaded.stale_factors() == md.snapshot.stale_factors()
        pid = repo.save_market_snapshot(md.previous_snapshot)
        assert [x[0] for x in repo.list_market_snapshots()] == [pid, sid]


def test_snapshot_with_values(md) -> None:
    s = md.previous_snapshot
    bumped = s.with_values({"EQIDX:SPX": s.index_level("SPX") * 0.8})
    assert bumped.index_level("SPX") == pytest.approx(s.index_level("SPX") * 0.8)
    assert bumped.snapshot_id != s.snapshot_id and isinstance(bumped, MarketSnapshot)


def test_extension_keeps_the_core_history_identical_and_joins_continuously() -> None:
    core = generate_market_data(MarketSimConfig(end_date=BD, years=1.0, seed=3, core_years=1.0))
    longer = generate_market_data(MarketSimConfig(end_date=BD, years=1.5, seed=3, core_years=1.0))
    a = core.history.pivot(index="as_of", columns="factor_id", values="value")
    b = longer.history.pivot(index="as_of", columns="factor_id", values="value")
    assert len(a) == 261 and len(b) == round(1.5 * 261)
    pd.testing.assert_frame_equal(b.loc[a.index], a)  # the last year is bit-identical
    assert b.index.max() == a.index.max() and b.index.min() < a.index.min()
    assert not b.isna().any().any()
    rates = [c for c in b.columns if c.startswith("IR:")]
    assert (b.drop(columns=rates) > 0).all().all()
    # The junction is an ordinary daily move: no factor jumps more than it ever moves elsewhere.
    change = b.diff().abs()
    junction = change.loc[a.index[0]]
    assert (junction <= change.drop(index=a.index[0]).max()).all()
    # Snapshots and planted problems still come from the core.
    assert longer.snapshot.as_of == BD and longer.planted == core.planted
