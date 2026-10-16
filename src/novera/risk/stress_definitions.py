"""Stress scenarios as stored definitions (OPS-005, MR-005).

A definition is one version of one scenario: what it shocks (hypothetical rules, a stylised
episode of the stored history, or a fixed historical window), whether it feeds the stress-loss
limits (goal LIMIT) or is reported for information (goal INFORMATION), and the governance facts
a scenario committee reads: tags, severity, narrative, catalyst, the area of the firm it was
written for. The store and its approval workflow live in ``novera.workflows.stress_setup``;
this module only describes, validates and resolves definitions into the ``StressScenario``
objects the engine revalues.

The built-in library is the hypothetical rules of ``HYPOTHETICAL_LIBRARY`` and the stylised
episodes stored with the history, all with goal LIMIT. Until a firm stores its own library the
built-ins run, so a database that predates the store produces exactly what it produced before.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

from novera.market_data.history import MarketHistory
from novera.risk.stress import (
    HYPOTHETICAL_LIBRARY,
    ShockRule,
    StressScenario,
    historical_episodes,
)

RECORD = "OPS-005"

GOALS = ("LIMIT", "INFORMATION")
KINDS = ("HYPOTHETICAL", "HISTORICAL")
SEVERITIES = ("MODERATE", "SEVERE", "EXTREME")
STATUSES = ("DRAFT", "APPROVED", "REJECTED", "SUPERSEDED")
TAGS = (
    # families of the stress framework (docs/08-stress-framework.md)
    "SENSITIVITY",
    "CROSS_ASSET",
    "HISTORICAL",
    "BASIS",
    "CORRELATION",
    "JUMP",
    "LIQUIDITY",
    "COUNTERPARTY",
    "CROWDING",
    "MACRO",
    "GEOPOLITICAL",
    "REVERSE",
    # asset classes
    "RATES",
    "FX",
    "EQUITY",
    "CREDIT",
    "COMMODITY",
    "CRYPTO",
    "VOLATILITY",
)
CURVE_TENORS = ("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

_BUILT_IN_TAGS: dict[str, tuple[str, ...]] = {
    "usd_rates_up_100": ("SENSITIVITY", "RATES"),
    "global_rates_up_100": ("SENSITIVITY", "RATES"),
    "usd_steepener": ("SENSITIVITY", "RATES"),
    "usd_flattener": ("SENSITIVITY", "RATES"),
    "eur_basis_wider_20": ("BASIS", "RATES"),
    "usd_funding_squeeze": ("BASIS", "FX", "LIQUIDITY"),
    "eurusd_down_10": ("SENSITIVITY", "FX"),
    "equity_crash_20_vol_15": ("CROSS_ASSET", "EQUITY", "VOLATILITY"),
    "credit_wider_150": ("SENSITIVITY", "CREDIT"),
    "oil_down_30": ("SENSITIVITY", "COMMODITY"),
    "btc_down_50": ("SENSITIVITY", "CRYPTO"),
    "vol_shock_10": ("SENSITIVITY", "VOLATILITY"),
    "global_risk_off": ("CROSS_ASSET", "MACRO"),
    "inflation_shock": ("CROSS_ASSET", "MACRO", "RATES"),
}


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    name: str
    kind: str  # HYPOTHETICAL or HISTORICAL
    goal: str = "INFORMATION"
    description: str = ""
    rules: tuple[dict[str, Any], ...] = ()
    """HYPOTHETICAL: ``ShockRule`` fields as dicts (prefix, size, vol_points, tenor_filter)."""
    episode_id: str | None = None
    """HISTORICAL: a stylised episode stored with the history (``market_episode``)."""
    window_start: str | None = None
    """HISTORICAL: a fixed window instead of an episode, ISO dates inside the stored history."""
    window_end: str | None = None
    enabled: bool = True
    tags: tuple[str, ...] = ()
    severity: str = "SEVERE"
    narrative: str = ""
    catalyst: str = ""
    area: str | None = None
    """The desk or business a desk head wrote the scenario for; None for the firm."""
    source: str = "USER"  # BUILT_IN or USER
    version: int = 1
    position: int = 0
    """Run order; the built-ins keep the order the engine always used."""
    resolved_window: tuple[str, str] | None = field(default=None, compare=False)
    """HISTORICAL: the dates the run actually replayed, recorded so a re-run reproduces them."""

    # --- (de)serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rules"] = [dict(r) for r in self.rules]
        d["tags"] = list(self.tags)
        d["resolved_window"] = list(self.resolved_window) if self.resolved_window else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScenarioDefinition:
        known = {f for f in cls.__dataclass_fields__}
        extra = set(d) - known
        if extra:
            raise ValueError(f"unknown fields: {', '.join(sorted(extra))}")
        rules = tuple(_rule_dict(r) for r in d.get("rules") or ())
        rw = d.get("resolved_window")
        return cls(
            **{
                **{k: v for k, v in d.items() if k in known},
                "rules": rules,
                "tags": tuple(d.get("tags") or ()),
                "version": int(d.get("version") or 1),
                "position": int(d.get("position") or 0),
                "resolved_window": (str(rw[0]), str(rw[1])) if rw else None,
            }
        )

    # --- engine --------------------------------------------------------------------
    def shock_rules(self) -> tuple[ShockRule, ...]:
        return tuple(
            ShockRule(
                r["prefix"],
                float(r.get("size") or 0.0),
                None if r.get("vol_points") is None else float(r["vol_points"]),
                tuple(r["tenor_filter"]) if r.get("tenor_filter") else None,
            )
            for r in self.rules
        )

    def to_scenario(self, window: tuple[date, date] | None = None) -> StressScenario:
        if self.kind == "HYPOTHETICAL":
            return StressScenario(
                self.scenario_id, self.name, self.description, "HYPOTHETICAL", self.shock_rules()
            )
        if window is None:
            raise ValueError(f"historical scenario {self.scenario_id} needs a window")
        description = (
            self.description or f"Observed moves from {window[0]} to {window[1]} in the simulated history"
        )
        return StressScenario(self.scenario_id, self.name, description, "HISTORICAL", episode=window)


def _rule_dict(r: dict[str, Any] | ShockRule) -> dict[str, Any]:
    if isinstance(r, ShockRule):
        r = asdict(r)
    out: dict[str, Any] = {"prefix": str(r["prefix"]), "size": float(r.get("size") or 0.0)}
    out["vol_points"] = None if r.get("vol_points") is None else float(r["vol_points"])
    tf = r.get("tenor_filter")
    out["tenor_filter"] = list(tf) if tf else None
    return out


def _episode_name(episode_id: str) -> str:
    return episode_id.replace("_", " ").title()


def built_in_definitions(episode_ids: list[str]) -> list[ScenarioDefinition]:
    """The library the platform ran before the store existed: every hypothetical rule set,
    then one definition per stylised episode, in that order, all feeding limits."""
    out = [
        ScenarioDefinition(
            sc.scenario_id,
            sc.name,
            "HYPOTHETICAL",
            goal="LIMIT",
            description=sc.description,
            rules=tuple(_rule_dict(r) for r in sc.rules),
            tags=_BUILT_IN_TAGS.get(sc.scenario_id, ()),
            source="BUILT_IN",
            position=i,
        )
        for i, sc in enumerate(HYPOTHETICAL_LIBRARY)
    ]
    n = len(out)
    out += [
        ScenarioDefinition(
            eid,
            _episode_name(eid),
            "HISTORICAL",
            goal="LIMIT",
            episode_id=eid,
            tags=("HISTORICAL", "CROSS_ASSET"),
            source="BUILT_IN",
            position=n + j,
        )
        for j, eid in enumerate(episode_ids)
    ]
    return out


def episode_windows(
    history: MarketHistory | None, stored: dict[str, tuple[date, date]] | None
) -> dict[str, tuple[date, date]]:
    """Stylised episode windows by id, oldest first: the stored ones, or for a database that
    predates them the core episodes counted back from the end of the history (decision 42.4)."""
    if stored:
        return dict(sorted(stored.items(), key=lambda kv: kv[1][0]))
    if history is None or not history.dates:
        return {}
    return {sc.scenario_id: sc.episode for sc in historical_episodes(history, None) if sc.episode}


@dataclass(frozen=True)
class Resolved:
    definition: ScenarioDefinition
    scenario: StressScenario


def resolve(
    definitions: list[ScenarioDefinition],
    windows: dict[str, tuple[date, date]],
    include_historical: bool = True,
) -> tuple[list[Resolved], list[str]]:
    """Definitions to runnable scenarios, in run order. Disabled definitions are skipped; a
    historical definition whose episode is unknown is skipped and named in the second list.
    A recorded ``resolved_window`` wins, so a re-run replays the dates the run replayed."""
    out: list[Resolved] = []
    unresolved: list[str] = []
    for d in sorted(definitions, key=lambda x: x.position):
        if not d.enabled:
            continue
        if d.kind == "HISTORICAL":
            if not include_historical:
                continue
            if d.resolved_window:
                w = (date.fromisoformat(d.resolved_window[0]), date.fromisoformat(d.resolved_window[1]))
            elif d.episode_id:
                if d.episode_id not in windows:
                    unresolved.append(d.scenario_id)
                    continue
                w = windows[d.episode_id]
            else:
                w = (date.fromisoformat(str(d.window_start)), date.fromisoformat(str(d.window_end)))
            out.append(
                Resolved(replace(d, resolved_window=(w[0].isoformat(), w[1].isoformat())), d.to_scenario(w))
            )
        else:
            out.append(Resolved(d, d.to_scenario()))
    return out, unresolved


def validate(
    d: ScenarioDefinition,
    factor_ids: list[str],
    episode_ids: list[str],
    history_range: tuple[date, date] | None,
) -> list[str]:
    """Everything wrong with a proposed definition, empty when it can run."""
    e: list[str] = []
    if not _ID.match(d.scenario_id):
        e.append("scenario_id must be 3-64 lower-case letters, digits or underscores, starting with a letter")
    if not d.name.strip():
        e.append("name is required")
    if d.kind not in KINDS:
        e.append(f"kind must be one of {', '.join(KINDS)}")
    if d.goal not in GOALS:
        e.append(f"goal must be one of {', '.join(GOALS)}")
    if d.severity not in SEVERITIES:
        e.append(f"severity must be one of {', '.join(SEVERITIES)}")
    bad_tags = [t for t in d.tags if t not in TAGS]
    if bad_tags:
        e.append(f"unknown tags: {', '.join(bad_tags)}")
    if d.kind == "HYPOTHETICAL":
        if not d.rules:
            e.append("a hypothetical scenario needs at least one shock rule")
        if d.episode_id or d.window_start or d.window_end:
            e.append("a hypothetical scenario takes rules, not a window")
        for i, r in enumerate(d.rules, 1):
            prefix = str(r.get("prefix") or "")
            if not prefix or not any(f.startswith(prefix) for f in factor_ids):
                e.append(f"rule {i}: {prefix or '(empty)'} matches no risk factor")
            vp, size = r.get("vol_points"), r.get("size") or 0.0
            if vp is not None and not prefix.startswith("VOL:"):
                e.append(f"rule {i}: vol points apply to VOL: surfaces only")
            for v in (size, vp):
                if v is not None and not math.isfinite(float(v)):
                    e.append(f"rule {i}: size must be a finite number")
            if (vp is None and float(size) == 0.0) or (vp is not None and float(vp) == 0.0):
                e.append(f"rule {i}: the shock is zero")
            tf = r.get("tenor_filter") or ()
            if tf and any(t not in CURVE_TENORS for t in tf):
                e.append(f"rule {i}: tenors must be among {', '.join(CURVE_TENORS)}")
    elif d.kind == "HISTORICAL":
        if d.rules:
            e.append("a historical scenario takes a window, not rules")
        if d.episode_id:
            if d.window_start or d.window_end:
                e.append("give an episode or a fixed window, not both")
            elif d.episode_id not in episode_ids:
                e.append(
                    f"unknown episode {d.episode_id}; stored episodes: {', '.join(episode_ids) or 'none'}"
                )
        else:
            try:
                s, t = date.fromisoformat(str(d.window_start)), date.fromisoformat(str(d.window_end))
            except ValueError:
                e.append("a historical scenario needs an episode or window_start and window_end (YYYY-MM-DD)")
            else:
                if s >= t:
                    e.append("window_start must be before window_end")
                elif history_range and (s < history_range[0] or t > history_range[1]):
                    e.append(
                        "the window must lie inside the stored history "
                        f"({history_range[0]} to {history_range[1]})"
                    )
    return e


# --- a run's stress stage ------------------------------------------------------------------
def from_recorded(d: dict[str, Any]) -> tuple[ScenarioDefinition, date | None]:
    """A definition as a run records it (``definitions_for_run``), with its review due date."""
    due = d.get("review_due")
    return (
        ScenarioDefinition.from_dict({k: v for k, v in d.items() if k != "review_due"}),
        date.fromisoformat(str(due)) if due else None,
    )


@dataclass
class StressRun:
    results: list[Any]
    """``StressResult`` per scenario, in run order."""
    resolved: list[Resolved]
    due: dict[str, date | None]
    unresolved: list[str]

    @property
    def limit_ids(self) -> set[str]:
        return {r.definition.scenario_id for r in self.resolved if r.definition.goal == "LIMIT"}

    @property
    def limit_results(self) -> list[Any]:
        """The results that feed the stress-loss limits (goal LIMIT)."""
        ids = self.limit_ids
        return [r for r in self.results if r.scenario.scenario_id in ids]

    @property
    def recorded(self) -> list[dict[str, Any]]:
        """What the run ran, for its config: each definition with the dates it replayed and its
        review due date, so a re-run reproduces the stage whatever the library says later."""
        out = []
        for r in self.resolved:
            due = self.due.get(r.definition.scenario_id)
            out.append({**r.definition.to_dict(), "review_due": due.isoformat() if due else None})
        return out

    def findings(self, as_of: date) -> list[Any]:
        """Data-quality findings: a review past due on the business date (MINOR), a historical
        scenario whose episode the history does not hold (MAJOR)."""
        from novera.data_quality.checks import Finding

        out = [
            Finding(
                "STRESS_REVIEW_OVERDUE",
                "MINOR",
                f"Stress scenario {r.definition.name} (v{r.definition.version}) was due for review on "
                f"{self.due[r.definition.scenario_id]}; it still runs (OPS-005)",
                r.definition.scenario_id,
                owner="Market Risk",
            )
            for r in self.resolved
            if (d := self.due.get(r.definition.scenario_id)) is not None and d < as_of
        ]
        out += [
            Finding(
                "STRESS_SCENARIO_UNRESOLVED",
                "MAJOR",
                f"Historical stress scenario {sid} names an episode the stored history does not hold; "
                "it did not run",
                sid,
                owner="Market Risk",
            )
            for sid in self.unresolved
        ]
        return out


def run_stress_definitions(
    pf: Any,
    history: MarketHistory,
    recorded: list[dict[str, Any]],
    stored_episodes: dict[str, tuple[date, date]] | None,
    include_historical: bool = True,
) -> StressRun:
    from novera.risk.stress import run_stress

    pairs = [from_recorded(d) for d in recorded]
    resolved, unresolved = resolve(
        [p[0] for p in pairs], episode_windows(history, stored_episodes), include_historical
    )
    due = {p[0].scenario_id: p[1] for p in pairs}
    results = run_stress(pf, [r.scenario for r in resolved], history)
    return StressRun(results, resolved, due, unresolved)


def stress_headline(results: list[Any], limit_results: list[Any], valuation: Any) -> dict[str, Any]:
    """The run summary's worst stress: of the LIMIT set (the headline the limits read), and of
    every scenario the run produced, information included (decision 46.3)."""
    from novera.risk.stress import stress_table

    def worst(rs: list[Any]) -> tuple[Any, Any, Any]:
        st = stress_table(rs, valuation) if rs else None
        if st is None or not len(st):
            return None, None, None
        w = st.iloc[0]
        return w["scenario_id"], w["name"], float(w["total"])

    a, b = worst(limit_results), worst(results)
    return {
        "worst_stress_id": a[0],
        "worst_stress_name": a[1],
        "worst_stress": a[2],
        "worst_stress_all_id": b[0],
        "worst_stress_all_name": b[1],
        "worst_stress_all": b[2],
        "stress_scenarios": len(results),
        "stress_limit_scenarios": len(limit_results),
    }
