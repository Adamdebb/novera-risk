"""Posted initial margin, MVA and exposure to CCPs (decision round 39)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from novera.counterparty_risk.ccp import (
    CCP_DEFAULT_FUND,
    CCP_MPOR_DAYS,
    QCCP_TRADE_RW,
    build_ccp_sets,
    ccp_netting_set_id,
    qccp_capital,
)
from novera.counterparty_risk.cva import CreditTerms, netting_set_xva
from novera.counterparty_risk.exposure import ExposureResult, collateralise
from novera.domain import CSA, ClearingType, NettingSet
from novera.market_data.curves import ZeroCurve
from novera.regulatory.simm import simm, simm_for_sensitivities


def _sens(rows):
    return pd.DataFrame(
        [{"trade_id": t, "measure": "DV01", "factor_id": "IR:USD:5Y", "bucket": "5Y", "underlying": "USD",
          "value": v} for t, v in rows]
    )  # fmt: skip


def _val(rows):
    return pd.DataFrame(
        [{"trade_id": t, "netting_set_id": ns, "counterparty_id": cp, "status": "LIVE"} for t, ns, cp in rows]
    )


def test_simm_both_directions_threshold_and_ccp() -> None:
    """Margin both ways (39.1): received on our exposure, posted on theirs (the same model on the
    sign-flipped sensitivities); exchanged only on CSA sets and above the threshold per
    counterparty, split pro rata; a CCP set posts its whole model margin and receives nothing."""
    sens = _sens([("a", 200_000.0), ("b", 100_000.0), ("c", 50_000.0), ("d", 300_000.0)])
    val = _val([("a", "NS1", "BANK"), ("b", "NS2", "BANK"), ("c", "NS3", "BANK"), ("d", "NS_CCP", "CCP_LCH")])
    one = simm_for_sensitivities(_sens([("a", 200_000.0)]), {})["IR"]
    res = simm(sens, val, {}, csa_sets={"NS1", "NS2", "NS_CCP"}, threshold=one, ccp_sets={"NS_CCP"})
    df = res.by_netting_set.set_index("netting_set_id")
    assert df.loc["NS1", "im_model"] == pytest.approx(one)
    gross = df.loc["NS1", "im_model"] + df.loc["NS2", "im_model"]
    assert df.loc["NS1", "im"] + df.loc["NS2", "im"] == pytest.approx(gross - one)  # above the threshold
    assert df.loc["NS1", "im"] / df.loc["NS2", "im"] == pytest.approx(
        df.loc["NS1", "im_model"] / df.loc["NS2", "im_model"]
    )
    assert df.loc["NS3", "im"] == 0.0 and df.loc["NS3", "im_posted"] == 0.0  # no CSA, no exchange
    assert df.loc["NS_CCP", "im"] == 0.0 and df.loc["NS_CCP", "im_posted"] == pytest.approx(
        df.loc["NS_CCP", "im_posted_model"]
    )
    assert df.loc["NS1", "im_posted_model"] == pytest.approx(
        df.loc["NS1", "im_model"]
    )  # linear risk: symmetric
    assert res.total == pytest.approx(df["im"].sum())


def _result(values: np.ndarray, ns: NettingSet, csa: CSA) -> ExposureResult:
    grid = [("1M", date(2026, 10, 15), 1 / 12), ("1Y", date(2027, 9, 15), 1.0)]
    return ExposureResult(grid, {ns.netting_set_id: values}, {ns.netting_set_id: 0.0},
                          {ns.netting_set_id: ns}, {csa.csa_id: csa}, {ns.netting_set_id: []}, values.shape[1])  # fmt: skip


def test_posted_margin_lowers_their_exposure_and_adds_to_a_ccps() -> None:
    """Bilateral: margin we post (segregated) lowers ENE and leaves our EE alone. At a CCP it is
    not bankruptcy-remote, so it adds to EE; ee_funding never carries it (FCA), the separate
    posted_margin column does (MVA)."""
    rng = np.random.default_rng(3)
    v = rng.normal(0.0, 10e6, size=(2, 2000))
    csa = CSA(
        csa_id="C",
        collateral_currency="USD",
        threshold_we_post=5e6,
        threshold_they_post=5e6,
        minimum_transfer_amount=0.0,
    )
    bil = NettingSet(netting_set_id="NS", counterparty_id="BANK", legal_entity_id="LE", csa_id="C")
    a = collateralise(_result(v, bil, csa), 10).set_index("step")
    b = collateralise(_result(v, bil, csa), 10, posted_margin={"NS": 3e6}).set_index("step")
    assert (b["ene"] < a["ene"]).all() and np.allclose(b["ee"], a["ee"])
    assert (b["posted_margin"] == 3e6).all()
    ccp_csa = CSA(csa_id="CC", collateral_currency="USD", threshold_we_post=0.0, threshold_they_post=0.0,
                  minimum_transfer_amount=0.0, margin_period_of_risk_days=CCP_MPOR_DAYS)  # fmt: skip
    ccp = NettingSet(
        netting_set_id="NSC",
        counterparty_id="CCP_LCH",
        legal_entity_id="LE",
        agreement_type="CCP",
        csa_id="CC",
    )
    c = collateralise(_result(v, ccp, ccp_csa), 10, posted_margin={"NSC": 3e6}).set_index("step")
    assert np.allclose(c["ee"], c["ee_funding"] + 3e6) and c["ccp"].all()


def test_mva_funds_posted_margin_and_joins_xva() -> None:
    years = np.array([0.5, 1.0])
    prof = pd.DataFrame({"years": years, "ee": [4e6, 4e6], "ene": [1e6, 1e6], "ee_funding": [1e6, 1e6],
                         "posted_margin": [3e6, 3e6]})  # fmt: skip
    flat = ZeroCurve("USD", np.array([1.0, 2.0]), np.array([0.0, 0.0]))
    cpty, own = CreditTerms(0.0, 0.6), CreditTerms(0.0, 0.6)
    x = netting_set_xva(prof, cpty, own, flat, 100.0)
    assert x["mva"] == pytest.approx(0.01 * 3e6 * 1.0) and x["fca"] == pytest.approx(0.01 * 1e6 * 1.0)
    assert x["xva"] == pytest.approx(x["cva"] - x["dva"] + x["fca"] + x["mva"])


def test_qccp_capital() -> None:
    """2% on trade exposure plus K_CM = max(K_CCP DF_i / DF_total, 8% x 2% x DF_i), times 8% for
    the trade part (CRE54, simplified)."""
    out = qccp_capital({"CCP_LCH": 100e6}, {"CCP_LCH": 50e6}).set_index("counterparty_id").loc["CCP_LCH"]
    df = CCP_DEFAULT_FUND["CCP_LCH"]
    assert out["trade_exposure"] == 150e6
    assert out["trade_exposure_capital"] == pytest.approx(150e6 * QCCP_TRADE_RW * 0.08)
    assert out["default_fund_capital"] == pytest.approx(df["k_ccp"] * df["contribution"] / df["total_fund"])
    assert out["capital"] == pytest.approx(out["trade_exposure_capital"] + out["default_fund_capital"])


def test_ccp_sets_group_cleared_trades(organisation, swap_trade) -> None:
    """One netting set per CCP and legal entity; bilateral trades and unknown books stay out."""
    book = organisation.books[0].book_id
    le = organisation.hierarchy_for_book(book)["legal_entity_id"]
    cleared = swap_trade.model_copy(
        update={
            "trade_id": "C1",
            "book_id": book,
            "clearing": ClearingType.CLEARED,
            "netting_set_id": None,
            "counterparty_id": "CCP_LCH",
        }  # fmt: skip
    )
    ghost = cleared.model_copy(update={"trade_id": "C2", "book_id": "NO_SUCH_BOOK"})
    sets = build_ccp_sets([cleared, ghost, swap_trade], organisation)
    assert [n.netting_set_id for n in sets.netting_sets] == [ccp_netting_set_id("CCP_LCH", le)]
    assert sets.by_trade == {"C1": ccp_netting_set_id("CCP_LCH", le)}
    assert (
        sets.netting_sets[0].agreement_type == "CCP"
        and sets.csas[0].margin_period_of_risk_days == CCP_MPOR_DAYS
    )
