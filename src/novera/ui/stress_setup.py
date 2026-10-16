"""Admin → Stress scenarios: the versioned stress library (OPS-005). A thin client: every number
and every rule comes from the API, every write goes through it."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

RULE_COLS = ["prefix", "size", "vol_points", "tenor_filter"]
PCA_COLS = ["pca", "component", "sigmas"]
LIST_COLS = [
    "scenario_id",
    "name",
    "kind",
    "goal",
    "version",
    "enabled",
    "severity",
    "tags",
    "review_due",
    "review_overdue",
    "area",
    "source",
]


def _rules_frame(rules: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "prefix": r.get("prefix"),
                "size": r.get("size"),
                "vol_points": r.get("vol_points"),
                "tenor_filter": ",".join(r.get("tenor_filter") or []),
            }
            for r in rules
            if "pca" not in r
        ],
        columns=RULE_COLS,
    )


def _pca_frame(rules: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{c: r.get(c) for c in PCA_COLS} for r in rules if "pca" in r and r.get("pca")], columns=PCA_COLS
    )


def _pca_from(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for row in frame.to_dict("records"):
        pref = row.get("pca")
        if isinstance(pref, str) and pref.strip() and not pd.isna(row.get("sigmas")):
            out.append(
                {
                    "pca": pref.strip(),
                    "component": int(row.get("component") or 1),
                    "sigmas": float(row["sigmas"]),
                }
            )
    return out


def _facts(f: dict[str, Any] | None) -> str:
    """One line of plausibility facts (MR-018) for the approver."""
    if not f:
        return "Plausibility: not available for this scenario."
    if f.get("error"):
        return f"Plausibility: {f['error']}"
    if f.get("z_score") is None:
        return "Plausibility: no key move to score."
    return (
        f"Plausibility ({f.get('horizon_days')}-day moves, {f.get('covariance')} covariance): z-score "
        f"{f['z_score']:.2f}, exceedance {f['exceedance']:.1%} of historical moves as extreme, correlation "
        f"z-score {f['correlation_z']:.2f} (above 1: the signs and sizes fight the correlations), risk "
        f"ruler {f.get('ruler_z') or 0:.2f}"
        + (f", {f['filled']} factors filled" if f.get("filled") else "")
        + "."
    )


def _rules_from(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for row in frame.to_dict("records"):
        prefix = row.get("prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            continue
        vp = row.get("vol_points")
        tf = row.get("tenor_filter")
        size = row.get("size")
        out.append(
            {
                "prefix": prefix.strip(),
                "size": 0.0 if size is None or pd.isna(size) else float(size),
                "vol_points": None if vp is None or pd.isna(vp) else float(vp),
                "tenor_filter": [t.strip() for t in tf.split(",") if t.strip()]
                if isinstance(tf, str) and tf.strip()
                else None,
            }
        )
    return out


def _version_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t["tags"] = t["tags"].map(lambda x: ", ".join(x or []))
    return t[[c for c in LIST_COLS if c in t.columns]]


def render_stress_setup(client: Any) -> None:
    from novera.api.errors import ApiError

    st.markdown(
        "The firm's stress library (OPS-005). Every change is a **draft** that a second person, the "
        "Head of Market Risk or the CRO, approves or rejects; only approved versions run. Goal **LIMIT** "
        "feeds the stress-loss limits; **INFORMATION** is reported only. Desk and business heads may "
        "propose information scenarios for their area. Each approved scenario is due for review every "
        "three months; an overdue one still runs and adds a MINOR data-quality finding to the run."
    )
    s = client.stress_setup()
    sm = s["summary"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("In the daily run", sm["in_daily_run"], f"of {sm['scenarios']} approved")
    c2.metric("Feed limits", sm["limit"])
    c3.metric("Information", sm["information"])
    c4.metric("Open drafts", sm["drafts"])
    c5.metric("Reviews overdue", sm["review_overdue"], f"business date {s['business_date']}")

    if s["source"] == "defaults":
        st.info(
            "The built-in library is running but not yet stored: no review dates. Storing it records "
            "the built-ins as approved under your name and starts their review clock. Any first "
            "proposal stores it too."
        )
        with st.form("stress_seed"):
            note = st.text_input("Note", key="stress_seed_note")
            if st.form_submit_button("Store the built-in library"):
                try:
                    client.seed_stress_library(note.strip())
                    st.success("Built-in library stored as approved.")
                    st.cache_data.clear()
                    st.rerun()
                except ApiError as e:
                    st.error(str(e))

    st.subheader("Approved scenarios, in run order")
    st.dataframe(_version_table(s["scenarios"]), use_container_width=True, hide_index=True)

    plaus: dict[str, Any] = {}
    show_ruler = st.checkbox(
        "Plausibility and risk ruler (MR-018)",
        value=bool(s["drafts"]),
        help="z-scores of every scenario and draft against stressed correlations, and the ruler that "
        "places them among the stored stress episodes. Computed on the latest snapshot; takes a few seconds.",
    )
    if show_ruler:
        plaus = client.stress_plausibility()
        if plaus.get("available"):
            rows = []
            for sid, f in plaus["scenarios"].items():
                if f and f.get("z_score") is not None:
                    rows.append(
                        {
                            "scenario_id": sid,
                            "z_score": round(f["z_score"], 2),
                            "exceedance": round(f["exceedance"], 3),
                            "correlation_z": round(f["correlation_z"], 2),
                            "ruler_z": round(f["ruler_z"] or 0.0, 2),
                            "covariance": f["covariance"],
                            "filled": f.get("filled"),
                        }
                    )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            ruler = pd.DataFrame(plaus["ruler"])
            if not ruler.empty:
                st.markdown(
                    f"**Risk ruler** · z-score on the headline basket, {plaus['horizon_days']}-day moves, "
                    "stressed correlations. Further right is less plausible."
                )
                st.bar_chart(ruler.set_index("name")["ruler_z"], horizontal=True)
        else:
            st.caption(plaus.get("reason") or "Plausibility is not available.")

    b1, b2 = st.columns(2)
    if b1.button(
        "Propose the built-in updates",
        help="Drafts of built-ins whose code version is newer than the stored one",
    ):
        try:
            out = client.upgrade_stress_built_ins("")
            st.success(f"{len(out)} built-in updates proposed for a second person to decide.")
            st.cache_data.clear()
            st.rerun()
        except ApiError as e:
            st.error(str(e))
    if b2.button(
        "Draft the calibrated crises", help="14 named crises with approximate headline moves to verify"
    ):
        try:
            out = client.draft_stress_crises()
            st.success(f"{len(out)} calibrated crises drafted; verify each figure before approving.")
            st.cache_data.clear()
            st.rerun()
        except ApiError as e:
            st.error(str(e))

    st.subheader(f"Drafts · {len(s['drafts'])}")
    if not s["drafts"]:
        st.caption("No draft is waiting for a decision.")
    for d in s["drafts"]:
        with st.expander(
            f"{d['name']} · {d['scenario_id']} v{d['version']} · {d['goal']} · proposed by {d['proposed_by']}"
        ):
            st.markdown(f"{d['description'] or ''}")
            if d.get("narrative") or d.get("catalyst"):
                st.caption(f"Narrative: {d.get('narrative') or '—'} · Catalyst: {d.get('catalyst') or '—'}")
            if d["kind"] == "HYPOTHETICAL":
                st.dataframe(_rules_frame(d["rules"]), use_container_width=True, hide_index=True)
                pca = _pca_frame(d["rules"])
                if not pca.empty:
                    st.dataframe(pca, use_container_width=True, hide_index=True)
                st.caption(
                    (
                        "Consistent: every factor the rules leave out moves with the "
                        + d["covariance"].lower()
                        + " correlations. "
                    )
                    if d.get("expand")
                    else "Standalone: factors the rules leave out stay where they are. "
                )
                if plaus.get("available"):
                    st.caption(_facts(plaus["drafts"].get(f"{d['scenario_id']}:{d['version']}")))
            else:
                st.caption(
                    f"Episode {d['episode_id']}"
                    if d.get("episode_id")
                    else f"Window {d['window_start']} to {d['window_end']}"
                )
            st.caption(f"Proposer's note: {d.get('note') or '—'} · enabled: {d['enabled']}")
            with st.form(f"decide_{d['scenario_id']}_{d['version']}"):
                note = st.text_input("Decision note", key=f"dn_{d['scenario_id']}_{d['version']}")
                a, r = st.columns(2)
                approve = a.form_submit_button("Approve")
                reject = r.form_submit_button("Reject")
            if approve or reject:
                try:
                    out = client.decide_stress_scenario(
                        d["scenario_id"], d["version"], bool(approve), note.strip()
                    )
                    st.success(f"{out['scenario_id']} v{out['version']} {out['status']}.")
                    st.cache_data.clear()
                    st.rerun()
                except ApiError as e:
                    st.error(str(e))

    st.subheader("Review")
    ids = [x["scenario_id"] for x in s["scenarios"]]
    with st.form("stress_review"):
        sid = st.selectbox("Scenario", ids, key="stress_review_id")
        note = st.text_input("Review note", key="stress_review_note")
        go = st.form_submit_button("Mark reviewed")
    if go:
        try:
            out = client.review_stress_scenario(sid, note.strip())
            st.success(f"{out['scenario_id']} reviewed; next review due {out['review_due']}.")
            st.cache_data.clear()
        except ApiError as e:
            st.error(str(e))

    st.subheader("Propose")
    opts = s["options"]
    start = st.selectbox("Start from", ["A new scenario", *ids], key="stress_start")
    base = next((x for x in s["scenarios"] if x["scenario_id"] == start), None) or {
        "scenario_id": "",
        "name": "",
        "kind": "HYPOTHETICAL",
        "goal": "INFORMATION",
        "description": "",
        "rules": [{"prefix": "", "size": 0.0, "vol_points": None, "tenor_filter": None}],
        "episode_id": None,
        "window_start": None,
        "window_end": None,
        "enabled": True,
        "expand": True,
        "covariance": "STRESSED",
        "horizon_days": 20,
        "tags": [],
        "severity": "SEVERE",
        "narrative": "",
        "catalyst": "",
    }
    kind = st.radio(
        "Kind", opts["kinds"], index=opts["kinds"].index(base["kind"]), horizontal=True, key=f"sk_{start}"
    )
    with st.form(f"stress_propose_{start}_{kind}"):
        c1, c2 = st.columns(2)
        scenario_id = c1.text_input(
            "Scenario id", base["scenario_id"], disabled=bool(start != "A new scenario")
        )
        name = c2.text_input("Name", base["name"])
        c3, c4, c5 = st.columns(3)
        goal = c3.selectbox("Goal", opts["goals"], index=opts["goals"].index(base["goal"]))
        severity = c4.selectbox(
            "Severity", opts["severities"], index=opts["severities"].index(base["severity"])
        )
        enabled = c5.checkbox("Run daily", value=bool(base["enabled"]))
        description = st.text_input("Description", base.get("description") or "")
        narrative = st.text_area("Narrative: what happens and why", base.get("narrative") or "")
        catalyst = st.text_input("Catalyst", base.get("catalyst") or "")
        tags = st.multiselect(
            "Tags", opts["tags"], default=[t for t in base.get("tags") or [] if t in opts["tags"]]
        )
        rules_frame = pd.DataFrame(columns=RULE_COLS)
        pca_frame = pd.DataFrame(columns=PCA_COLS)
        expand = covariance = horizon = None
        episode_id = window_start = window_end = None
        if kind == "HYPOTHETICAL":
            st.caption(
                "Rules in engine units: rates and basis as decimals (0.01 = +100bp), CDS in bp, prices "
                "as fractions (-0.20 = -20%), vol surfaces in vol points (0.10 = +10). Prefix a family "
                "(IR:USD:, EQIDX:, CDS:) or name one factor; tenors limit a curve rule (1Y,2Y)."
            )
            rules_frame = st.data_editor(
                _rules_frame(base["rules"] or []),
                num_rows="dynamic",
                hide_index=True,
                use_container_width=True,
                key=f"rules_{start}",
                column_config={
                    "prefix": st.column_config.TextColumn("Prefix or factor", required=True),
                    "size": st.column_config.NumberColumn("Size", format="%.4f"),
                    "vol_points": st.column_config.NumberColumn("Vol points", format="%.3f"),
                    "tenor_filter": st.column_config.TextColumn("Tenors", help="Comma separated, e.g. 1Y,2Y"),
                },
            )
            st.caption(
                "PCA shapes (optional): a curve or surface prefix (IR:USD:, CMD:BRENT:, VOL:SPX:), the "
                "component (1 level, 2 slope, 3 curvature) and its size in standard deviations over "
                "the horizon."
            )
            pca_frame = st.data_editor(
                _pca_frame(base["rules"] or []),
                num_rows="dynamic",
                hide_index=True,
                use_container_width=True,
                key=f"pca_{start}",
                column_config={
                    "pca": st.column_config.TextColumn("Curve or surface prefix"),
                    "component": st.column_config.NumberColumn("Component", min_value=1, max_value=3, step=1),
                    "sigmas": st.column_config.NumberColumn("Size (σ)", format="%.2f"),
                },
            )
            e1, e2, e3 = st.columns(3)
            expand = e1.checkbox(
                "Consistent (move every factor)",
                value=bool(base.get("expand", False)),
                help="Factors the rules leave out take their conditional expectation given the rules' "
                "key moves (MR-018).",
            )
            covs = opts.get("covariances") or ["STRESSED", "SIMILAR", "RECENT"]
            covariance = e2.selectbox(
                "Covariance", covs, index=covs.index(base.get("covariance") or "STRESSED")
            )
            horizon = e3.number_input(
                "Horizon (business days)",
                min_value=5,
                max_value=250,
                value=int(base.get("horizon_days") or 20),
            )
        else:
            eps = ["(fixed window)", *opts["episodes"]]
            pick = st.selectbox(
                "Episode", eps, index=eps.index(base["episode_id"]) if base.get("episode_id") in eps else 0
            )
            if pick == "(fixed window)":
                w1, w2 = st.columns(2)
                window_start = w1.text_input("Window start (YYYY-MM-DD)", base.get("window_start") or "")
                window_end = w2.text_input("Window end (YYYY-MM-DD)", base.get("window_end") or "")
                st.caption(
                    "The window must lie inside the stored history "
                    f"({s['history_start']} to {s['history_end']})."
                )
            else:
                episode_id = pick
        note = st.text_input("Note for the approver", key=f"pn_{start}")
        submitted = st.form_submit_button("Propose")
    if submitted:
        definition = {
            "scenario_id": (scenario_id or "").strip(),
            "name": name.strip(),
            "kind": kind,
            "goal": goal,
            "description": description.strip(),
            "rules": (_rules_from(rules_frame) + _pca_from(pca_frame)) if kind == "HYPOTHETICAL" else [],
            "expand": bool(expand) if kind == "HYPOTHETICAL" else False,
            "covariance": covariance or "STRESSED",
            "horizon_days": int(horizon or 20),
            "episode_id": episode_id,
            "window_start": window_start or None,
            "window_end": window_end or None,
            "enabled": enabled,
            "tags": tags,
            "severity": severity,
            "narrative": narrative.strip(),
            "catalyst": catalyst.strip(),
        }
        try:
            out = client.propose_stress_scenario(definition, note.strip())
            st.success(
                f"Draft {out['scenario_id']} v{out['version']} proposed. A second person, the Head of Market "
                "Risk or the CRO, approves it before it runs."
            )
            st.cache_data.clear()
        except ApiError as e:
            st.error(str(e))

    with st.expander(f"Decided history · {len(s['history'])}"):
        h = pd.DataFrame(s["history"])
        if h.empty:
            st.caption("Nothing superseded or rejected yet.")
        else:
            st.dataframe(
                h[
                    [
                        "scenario_id",
                        "version",
                        "status",
                        "goal",
                        "proposed_by",
                        "decided_by",
                        "decided_at",
                        "note",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
