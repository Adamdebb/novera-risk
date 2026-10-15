"""Limits calibrated from a target utilisation (decision round 40, MR-017)."""

from datetime import date

import pandas as pd
import pytest

from novera.domain.breaches import Breach, BreachStatus
from novera.domain.enums import HierarchyLevel
from novera.domain.limits import LimitStatus, LimitType
from novera.limits.calibration import STORY_TARGETS, TARGETS, propose, round_amount
from novera.simulation.limits import _lim
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.limit_calibration import decide_draft, propose_recalibration
from novera.workflows.runs import RunRecord

D = date(2026, 9, 11)


def _limits():
    return [
        _lim("DESK_DV01", LimitType.DV01, HierarchyLevel.DESK, "USD_RATES", 1.0e6, "Head of USD Rates"),
        _lim("FIRM_VAR", LimitType.VAR, HierarchyLevel.FIRM, "GMB", 50e6, "CRO"),
        _lim(
            "USD_RATES_DV01_USD_10Y",
            LimitType.DV01,
            HierarchyLevel.DESK,
            "USD_RATES",
            1.1e6,
            "Head of USD Rates",
        ),
        _lim("EMPTY_VEGA", LimitType.VEGA, HierarchyLevel.DESK, "FX", 1e5, "Head of FX"),
        _lim("HOT_CS01", LimitType.CS01, HierarchyLevel.DESK, "CREDIT", 1e5, "Head of Credit"),
    ]


def _measured():
    return pd.DataFrame(
        [
            {"limit_id": "DESK_DV01", "current": 861_000.0, "status": "WARNING"},
            {"limit_id": "FIRM_VAR", "current": -45e6, "status": "OK"},
            {"limit_id": "USD_RATES_DV01_USD_10Y", "current": 2_166_000.0, "status": "BREACH"},
            {"limit_id": "EMPTY_VEGA", "current": 0.0, "status": "OK"},
            {"limit_id": "HOT_CS01", "current": 130_000.0, "status": "BREACH"},
        ]
    )


def test_round_amount() -> None:
    assert round_amount(1_230_417) == 1_250_000 and round_amount(8_700_000) == 8_750_000
    assert round_amount(62e6) == 62.5e6 and round_amount(0.587) == pytest.approx(0.6)  # always up
    assert round_amount(1_000_000) == 1_000_000


def test_proposals_follow_type_and_story_targets() -> None:
    """amount = |measure| / target: rounded for a type target, exact for a story limit so it lands
    on its designed utilisation; nothing in scope keeps its amount; at seeding a breach on the
    provisional amount means nothing, on a recalibration it is left alone."""
    seed = {p.limit.limit_id: p for p in propose(_limits(), _measured(), guard_breaches=False)}
    assert seed["DESK_DV01"].new_amount == round_amount(861_000 / TARGETS[LimitType.DV01])
    assert seed["FIRM_VAR"].new_amount == round_amount(45e6 / TARGETS[LimitType.VAR])
    story = seed["USD_RATES_DV01_USD_10Y"]
    assert story.story and story.new_utilisation == pytest.approx(STORY_TARGETS["USD_RATES_DV01_USD_10Y"])
    assert seed["EMPTY_VEGA"].new_amount is None and seed["HOT_CS01"].new_amount is not None
    recal = {p.limit.limit_id: p for p in propose(_limits(), _measured(), guard_breaches=True)}
    assert recal["HOT_CS01"].new_amount is None and "increase request" in recal["HOT_CS01"].reason
    assert recal["USD_RATES_DV01_USD_10Y"].new_amount is None


@pytest.fixture()
def repo():
    r = DuckDBRepository(":memory:")
    r.init_schema()
    r.save_limits(_limits())
    run = RunRecord("run_ref", "EOD", D, "pf", "mkt", None, "USD", {}, {})
    run.status = "COMPLETED"
    r.save_run(run)
    r.save_run_frame("run_ref", "limits", _measured())
    yield r
    r.close()


def test_recalibration_is_a_governed_draft(repo) -> None:
    """Drafts start the next business day; the proposer cannot decide; only the level's approver
    can approve; an approved draft ends the old version the day before and keeps it in history;
    a limit with an open breach is refused."""
    drafts, left = propose_recalibration(repo, "run_ref", "Risk Methodology")
    by_id = {d["limit_id"]: d for d in drafts}
    assert set(by_id) == {"DESK_DV01", "FIRM_VAR"} and by_id["DESK_DV01"]["effective_from"] == "2026-09-14"
    assert {p.limit.limit_id for p in left} >= {"HOT_CS01", "USD_RATES_DV01_USD_10Y", "EMPTY_VEGA"}
    with pytest.raises(ValueError, match="cannot decide"):
        decide_draft(repo, by_id["DESK_DV01"]["draft_id"], "Risk Methodology", True)
    with pytest.raises(ValueError, match="cannot approve"):
        decide_draft(repo, by_id["FIRM_VAR"]["draft_id"], "Head of Market Risk", True)
    d = decide_draft(repo, by_id["DESK_DV01"]["draft_id"], "Head of Market Risk", True, "methodology change")
    assert d["status"] == "APPROVED"
    before = {lim.limit_id: lim for lim in repo.load_limits(on=date(2026, 9, 11))}
    after = {lim.limit_id: lim for lim in repo.load_limits(on=date(2026, 9, 14))}
    assert before["DESK_DV01"].amount == 1.0e6 and before["DESK_DV01"].effective_to == date(2026, 9, 13)
    assert after["DESK_DV01"].amount == by_id["DESK_DV01"]["new_amount"]
    assert (
        after["DESK_DV01"].status is LimitStatus.APPROVED
        and after["DESK_DV01"].approver == "Head of Market Risk"
    )
    assert len(repo.limit_versions("DESK_DV01")) == 1
    repo.save_breach(
        Breach(
            breach_id="b1",
            limit_id="FIRM_VAR",
            limit_type="VAR",
            level="FIRM",
            entity_id="GMB",
            owner="CRO",
            status=BreachStatus.OPEN,
            first_run_id="run_ref",
            latest_run_id="run_ref",
            first_date=D,
            latest_date=D,
        )  # fmt: skip
    )
    with pytest.raises(ValueError, match="open breach"):
        decide_draft(repo, by_id["FIRM_VAR"]["draft_id"], "CRO", True)
    assert (
        decide_draft(repo, by_id["FIRM_VAR"]["draft_id"], "CRO", False, "breach first")["status"]
        == "REJECTED"
    )
    assert not repo.load_limit_drafts(status="PENDING")


def test_calibration_stays_within_the_appetite_band() -> None:
    """Decision 40.6: a desk using a sliver of its designed amount keeps half of it; one running
    hot gets at most double; within the band the amount follows the target, rounded up so the
    limit never opens above it."""
    from novera.limits.calibration import APPETITE_BAND

    lims = [
        _lim("QUIET_STRESS", LimitType.STRESS_LOSS, HierarchyLevel.DESK, "HY", 30e6, "Head of HY"),
        _lim("HOT_DELTA", LimitType.FX_DELTA, HierarchyLevel.DESK, "G10", 1e6, "Head of FX"),
        _lim("MID_VEGA", LimitType.VEGA, HierarchyLevel.DESK, "EQ", 5e5, "Head of EQ"),
    ]
    measured = pd.DataFrame(
        [
            {"limit_id": "QUIET_STRESS", "current": 230_000.0, "status": "OK"},
            {"limit_id": "HOT_DELTA", "current": 9e6, "status": "WARNING"},
            {"limit_id": "MID_VEGA", "current": 400_000.0, "status": "WARNING"},
        ]
    )
    p = {x.limit.limit_id: x for x in propose(lims, measured, guard_breaches=False)}
    assert (
        p["QUIET_STRESS"].new_amount == APPETITE_BAND[0] * 30e6 == 30e6
        and "appetite" in p["QUIET_STRESS"].reason
    )
    assert p["HOT_DELTA"].new_amount == APPETITE_BAND[1] * 1e6 and "capped" in p["HOT_DELTA"].reason
    assert p["MID_VEGA"].new_amount == round_amount(400_000 / 0.70) and p["MID_VEGA"].new_utilisation <= 0.70
    assert p["MID_VEGA"].new_amount >= 5e5  # never below its appetite
    # A recalibration reads the designed amount kept on the limit, not the calibrated one.
    calibrated = lims[0].model_copy(update={"amount": 45e6, "appetite": 30e6})
    again = propose([calibrated], measured.iloc[:1], guard_breaches=True)[0]
    assert again.new_amount == 30e6  # back to the appetite, not to half of the calibrated amount
