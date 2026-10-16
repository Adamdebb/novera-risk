"""Admin → Stress scenarios: the versioned stress library (OPS-005). A thin client: every number
and every rule comes from the API, every write goes through it."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

RULE_COLS = ["prefix", "size", "vol_points", "tenor_filter"]
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
        ],
        columns=RULE_COLS,
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
            "The built-in library is running but not yet stored: no versions, no review dates. Storing "
            "it records the built-ins as approved version 1 under your name and starts their review "
            "clock. Any first proposal stores it too."
        )
        with st.form("stress_seed"):
            note = st.text_input("Note", key="stress_seed_note")
            if st.form_submit_button("Store the built-in library"):
                try:
                    client.seed_stress_library(note.strip())
                    st.success("Built-in library stored as approved version 1.")
                    st.cache_data.clear()
                    st.rerun()
                except ApiError as e:
                    st.error(str(e))

    st.subheader("Approved scenarios, in run order")
    st.dataframe(_version_table(s["scenarios"]), use_container_width=True, hide_index=True)

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
            "rules": _rules_from(rules_frame) if kind == "HYPOTHETICAL" else [],
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
