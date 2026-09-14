"""Streamlit thin client: the morning risk dashboard and its drill-downs.

Holds no business logic. Every number on screen comes from the client (local service or
HTTP API) and carries the run id it was computed in (ADR 0004).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from novera.api.client import make_client
from novera.config import get_settings

settings = get_settings()
st.set_page_config(page_title=f"{settings.platform_name} Risk", page_icon="📊", layout="wide")
client = make_client(settings)

M = 1e6


def money(x: float | None, unit: str = "m", digits: int = 1) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x / M:,.{digits}f}{unit}"


def df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def load_runs() -> list[dict]:
    return client.runs(30)


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, run_id: str, **kw):
    return getattr(client, name)(run_id=run_id, **kw)


# --- sidebar: run selection -----------------------------------------------------------
runs = load_runs()
if not runs:
    st.title(f"{settings.platform_name}")
    st.warning("No completed run found. Run `novera simulate` then `novera run eod`.")
    st.stop()
labels = {r["run_id"]: f"{r['business_date']}  {r['run_id']}  [{r['verdict']}]" for r in runs}
with st.sidebar:
    st.markdown(f"## {settings.platform_name}")
    st.caption(settings.platform_tagline)
    run_id = st.selectbox("Run", list(labels), format_func=labels.get)
    page = st.radio(
        "View",
        [
            "Overview",
            "Copilot",
            "Drill-down",
            "VaR",
            "Stress",
            "Limits",
            "Breaches",
            "P&L explain",
            "Data quality",
            "Concentration & liquidity",
            "Compare runs",
            "Challenger",
            "Risk pack",
            "Alerts & jobs",
            "Runs & audit",
        ],
    )
    st.caption("Every figure is read from the stored run. Nothing is computed on this page.")

summary = load("summary", run_id)
sm = summary["summary"]
ccy = summary["reporting_currency"]
verdict_colour = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}.get(summary["verdict"], "⚪")


def header(title: str) -> None:
    st.title(title)
    st.caption(
        f"Business date {summary['business_date']} · run {run_id} · verdict {verdict_colour} "
        f"{summary['verdict']} · portfolio {summary['portfolio_snapshot_id']} · market "
        f"{summary['market_snapshot_id']} · models "
        f"{', '.join(f'{k} {v}' for k, v in summary['model_versions'].items())}"
    )


# --- pages ------------------------------------------------------------------------------
if page == "Overview":
    header("Global market risk")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("VaR 99% 1d", money(sm["var"], digits=2))
    c2.metric("ES 97.5%", money(sm["es"], digits=2))
    c3.metric("Worst stress", money(sm["worst_stress"]), sm["worst_stress_name"])
    firm_var = next((r for r in load("limits", run_id) if r["limit_id"] == "FIRM_VAR"), None)
    c4.metric(
        "VaR limit utilisation",
        f"{firm_var['utilisation']:.0%}" if firm_var else "—",
        f"limit {money(firm_var['amount'])}" if firm_var else None,
    )
    c5.metric("P&L today", money(sm.get("pnl_total"), digits=2))
    c6.metric("Data quality", f"{verdict_colour} {sm['dq_verdict']}", f"{sm['dq_findings']} findings")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Largest risk contributors")
        vac = df(summary["var_by_asset_class"])
        if not vac.empty:
            vac["component VaR (m)"] = vac["component_var"] / M
            st.bar_chart(vac.set_index("asset_class")["component VaR (m)"])
        st.subheader("Day-on-day P&L explain")
        steps = summary.get("pnl_steps") or {}
        if steps:
            sdf = pd.DataFrame({"step": list(steps), "pnl (m)": [v / M for v in steps.values()]})
            st.bar_chart(sdf.set_index("step")["pnl (m)"])
    with right:
        st.subheader("Limit breaches")
        breaches = df(load("limits", run_id, status="BREACH"))
        if breaches.empty:
            st.success("No breaches")
        else:
            for _, r in breaches.iterrows():
                unit = "" if r["limit_type"] == "CONCENTRATION" else "m"
                scale = 1 if r["limit_type"] == "CONCENTRATION" else M
                st.error(
                    f"**{r['limit_id']}** — {r['current'] / scale:,.2f}{unit} / "
                    f"{r['amount'] / scale:,.2f}{unit} "
                    f"({r['utilisation']:.0%}) · owner {r['owner']}"
                )
        warnings = df(load("limits", run_id, status="WARNING"))
        if not warnings.empty:
            st.warning(
                f"{len(warnings)} limits in warning, highest: "
                + ", ".join(f"{r['limit_id']} {r['utilisation']:.0%}" for _, r in warnings.head(4).iterrows())
            )
        st.subheader("Can I trust today's run?")
        dq = df(summary["dq_findings"])
        if dq.empty:
            st.success("No data-quality findings")
        else:
            for _, r in dq.iterrows():
                fn = st.error if r["severity"] in ("CRITICAL", "MAJOR") else st.info
                fn(f"**{r['code']}** {r['message']} ({r['affected_trades']} trades) · owner {r['owner']}")

elif page == "Copilot":
    header("Risk Copilot")
    prov = client.copilot_provider()
    if prov["provider"] == "scripted":
        st.info(
            "Running the scripted provider: answers are templated from stored numbers. Set "
            "ANTHROPIC_API_KEY in .env to switch to Claude."
        )
    else:
        st.caption(
            f"Provider {prov['provider']} · model {prov['model']} · answers cite run ids and are stored "
            "with their tool calls."
        )
    if "copilot_chat" not in st.session_state:
        st.session_state.copilot_chat = []
        st.session_state.copilot_session = f"ui_{run_id}"
    examples = [
        "Why did VaR change since yesterday?",
        "Which books are closest to their limits?",
        "What happens if equities fall 20%, vol rises 15 points, oil drops 30% and BTC falls 40%?",
        "Can I trust today's run?",
        "Draft the morning commentary",
    ]
    cols = st.columns(len(examples))
    picked = None
    for col, ex in zip(cols, examples, strict=True):
        if col.button(ex, key=f"ex_{ex[:20]}"):
            picked = ex
    for turn in st.session_state.copilot_chat:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("tool_calls"):
                with st.expander(
                    f"{len(turn['tool_calls'])} tool calls · {turn['seconds']}s · runs "
                    f"{', '.join(turn['run_ids'])}"
                ):
                    for tc in turn["tool_calls"]:
                        st.code(
                            f"{tc['name']}({json.dumps(tc['input'])})\n{tc['output'][:1500]}", language="json"
                        )
    question = st.chat_input("Ask about this run") or picked
    if question:
        st.session_state.copilot_chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.spinner("Reading the run…"):
            if question.lower().startswith("draft the morning commentary"):
                ans = client.commentary(run_id)
            else:
                ans = client.ask(question, run_id=run_id, session_id=st.session_state.copilot_session)
            st.markdown(ans["answer"])
        st.session_state.copilot_chat.append(
            {
                "role": "assistant",
                "content": ans["answer"],
                "tool_calls": ans["tool_calls"],
                "seconds": ans["seconds"],
                "run_ids": ans["run_ids_cited"],
            }
        )
        st.rerun()

elif page == "Drill-down":
    header("Risk by hierarchy")
    org = client.organisation()
    level = st.selectbox(
        "Level",
        [
            "business_id",
            "desk_id",
            "book_id",
            "trader_id",
            "legal_entity_id",
            "asset_class",
            "product_type",
            "currency",
            "counterparty_id",
        ],
        index=1,
    )
    desk = st.selectbox("Filter desk", ["(all)"] + [d["desk_id"] for d in org["desks"]])
    filters = {} if desk == "(all)" else {"desk_id": desk}
    pos = df(load("positions", run_id, by=level, **filters))
    var = df(load("var_by", run_id, by=level, **filters))
    if not pos.empty:
        table = (
            pos.merge(var.drop(columns=["trades"], errors="ignore"), on=level, how="left")
            if not var.empty
            else pos
        )
        table["pv (m)"] = table["pv"] / M
        if "component_var" in table:
            table["component VaR (m)"] = table["component_var"] / M
            table["component ES (m)"] = table["component_es"] / M
        cols = [level, "pv (m)", "trades", "unpriced"] + [
            c for c in ("component VaR (m)", "component ES (m)") if c in table
        ]
        st.dataframe(
            table[cols].sort_values(cols[-1] if len(cols) > 4 else "pv (m)", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("DV01 ladder (per bp)")
    measure = st.selectbox(
        "Measure",
        ["DV01", "CS01", "FX_DELTA", "EQ_DELTA", "CMD_DELTA", "CRYPTO_DELTA", "VEGA", "GAMMA", "THETA"],
    )
    sens = df(load("sensitivities", run_id, measure=measure, by=level, **filters))
    if not sens.empty:
        if measure == "DV01" and "bucket" in sens:
            order = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
            piv = sens.pivot_table(
                index=[level, "underlying"], columns="bucket", values="value", aggfunc="sum", fill_value=0.0
            )
            piv = piv.reindex(columns=[b for b in order if b in piv.columns])
            st.dataframe((piv / 1e3).round(1).rename(columns=lambda c: f"{c} (k)"), use_container_width=True)
        else:
            st.dataframe(
                sens.assign(value_k=lambda d: d["value"] / 1e3).drop(columns=["value"]),
                use_container_width=True,
                hide_index=True,
            )
    st.subheader("Trade lookup")
    tid = st.text_input("Trade id", placeholder="IRS_000201")
    if tid:
        try:
            t = client.trade(tid, run_id=run_id)
            c1, c2 = st.columns(2)
            c1.json(t["valuation"])
            c2.json(t["trade"])
            st.dataframe(df(t["sensitivities"]), use_container_width=True, hide_index=True)
        except Exception as e:  # noqa: BLE001
            st.error(f"not found: {e}")

elif page == "VaR":
    header("Value at Risk")
    vs = df(load("var_summary", run_id))
    c1, c2 = st.columns(2)
    for col, (_, r) in zip((c1, c2), vs.iterrows(), strict=False):
        col.metric(
            r["method"].replace("_", " "),
            money(r["var"], digits=2),
            f"ES {money(r['es'], digits=2)} · 10d {money(r['var_scaled'])} · {int(r['scenarios'])} scenarios",
        )
    by = st.selectbox("Component VaR by", ["asset_class", "business_id", "desk_id", "book_id", "currency"])
    prim = df(load("var_by", run_id, by=by)).set_index(by)
    chal = df(load("var_by", run_id, by=by, method="delta_gamma_vega")).set_index(by)
    comp = pd.DataFrame(
        {"full revaluation (m)": prim["component_var"] / M, "delta-gamma-vega (m)": chal["component_var"] / M}
    ).fillna(0.0)
    comp["difference (m)"] = comp.iloc[:, 1] - comp.iloc[:, 0]
    st.dataframe(comp.round(2), use_container_width=True)
    st.caption(
        "Where the challenger disagrees, the gap is convexity, cross effects and surface shape "
        "that sensitivities miss (MR-004)."
    )
    sc = df(load("var_scenarios", run_id))
    if not sc.empty:
        sc["scenario_date"] = pd.to_datetime(sc["scenario_date"])
        st.subheader("Scenario P&L distribution")
        st.line_chart(sc.set_index("scenario_date")[["portfolio_pnl", "challenger_pnl"]] / M)
        worst = sc.nsmallest(10, "portfolio_pnl")
        st.dataframe(
            worst.assign(
                **{
                    "portfolio pnl (m)": worst["portfolio_pnl"] / M,
                    "challenger (m)": worst["challenger_pnl"] / M,
                }
            )[["scenario_date", "portfolio pnl (m)", "challenger (m)"]],
            hide_index=True,
        )

    bt = client.backtest(run_id)
    if bt["summary"]:
        st.subheader("Backtest")
        bs = df(bt["summary"])
        st.dataframe(
            bs[
                [
                    "kind",
                    "days",
                    "exceptions",
                    "expected_exceptions",
                    "kupiec_pvalue",
                    "christoffersen_pvalue",
                    "conditional_pvalue",
                    "zone",
                ]
            ].round(3),
            use_container_width=True,
            hide_index=True,
        )
        ser = df(bt["series"])
        if not ser.empty:
            ser["date"] = pd.to_datetime(ser["date"])
            chart = ser.set_index("date")[["pnl", "var"]] / M
            chart["-var"] = -chart["var"]
            st.line_chart(chart[["pnl", "-var"]])
            exc = ser[ser["exception"]]
            if not exc.empty:
                st.caption("Exceptions on: " + ", ".join(exc["date"].dt.strftime("%Y-%m-%d")))
        st.caption(
            "Static-portfolio hypothetical backtest: today's book against the last 250 daily moves, VaR "
            "from the preceding 250 (MR-011). The live series grows one point per stored run."
        )

elif page == "Stress":
    header("Stress testing")
    by = st.selectbox("Loss by", ["asset_class", "business_id", "desk_id", "book_id"])
    stt = df(load("stress", run_id, by=by))
    if not stt.empty:
        cols = [c for c in stt.columns if c not in ("scenario_id", "name", "kind", "description", "total")]
        show = stt[["name", "kind", "total", *cols]].copy()
        for c in ["total", *cols]:
            show[c] = show[c] / M
        st.dataframe(
            show.round(1).rename(columns={"total": "total (m)"}), use_container_width=True, hide_index=True
        )
        pick = st.selectbox("Scenario", stt["name"])
        row = stt[stt["name"] == pick].iloc[0]
        st.caption(row["description"])
        st.bar_chart(pd.Series({c: row[c] / M for c in cols}, name="loss (m)"))

elif page == "Limits":
    header("Limits")
    status = st.multiselect("Status", ["BREACH", "WARNING", "OK", "NO_DATA"], default=["BREACH", "WARNING"])
    lt = df(load("limits", run_id))
    if not lt.empty:
        lt = lt[lt["status"].isin(status)]
        show = lt[
            [
                "status",
                "limit_id",
                "limit_type",
                "level",
                "entity_id",
                "filters",
                "current",
                "amount",
                "utilisation",
                "owner",
                "trades_in_scope",
            ]
        ].copy()
        conc = show["limit_type"] == "CONCENTRATION"
        show.loc[~conc, "current"] = show.loc[~conc, "current"] / M
        show.loc[~conc, "amount"] = show.loc[~conc, "amount"] / M
        show["utilisation"] = (show["utilisation"] * 100).round(0)
        st.dataframe(
            show.round(2).rename(
                columns={
                    "current": "current (m, or share)",
                    "amount": "limit (m, or share)",
                    "utilisation": "utilisation %",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

elif page == "P&L explain":
    header("Daily P&L explain")
    by = st.selectbox("Attribution by", ["asset_class", "business_id", "desk_id", "book_id"])
    p = load("pnl", run_id, by=by)
    steps = df(p["steps"])
    if not steps.empty:
        st.metric("Total P&L", money(p["total"], digits=2))
        st.bar_chart(steps.set_index("step")["pnl"] / M)
    if "by" in p:
        byt = df(p["by"])
        num = [c for c in byt.columns if c != by]
        byt[num] = byt[num] / M
        st.dataframe(byt.round(2), use_container_width=True, hide_index=True)
    ch = p.get("challenger")
    if ch:
        c1, c2, c3 = st.columns(3)
        c1.metric("Official (full revaluation)", money(ch["actual"], digits=2))
        c2.metric("Sensitivity-based", money(ch["predicted"], digits=2))
        c3.metric("Unexplained", money(ch["residual"], digits=2))
        st.subheader("Largest unexplained by trade")
        st.dataframe(
            df(ch["worst_residuals"])
            .assign(**{c: lambda d, c=c: d[c] / M for c in ("actual", "predicted", "residual")})
            .round(3),
            hide_index=True,
            use_container_width=True,
        )

elif page == "Breaches":
    header("Breach workflow")
    from novera.limits import WorkflowError

    def act(fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
            st.cache_data.clear()
            st.rerun()
        except WorkflowError as e:
            st.error(str(e))

    show_closed = st.checkbox("Show closed breaches", value=False)
    breaches = client.breaches(open_only=not show_closed)
    if not breaches:
        st.success("No open breaches.")
    for b in breaches:
        badge = {"OPEN": "🔴", "ACKNOWLEDGED": "🟠", "ESCALATED": "🟣", "CLOSED": "⚪"}[b["status"]]
        title = (
            f"{badge} {b['status']} · {b['limit_id']} · {b['latest_utilisation']:.0%} · day "
            f"{b['consecutive_days']} · owner {b['owner']}"
            + (f" · escalated to {b['escalated_to']}" if b.get("escalated_to") else "")
            + (" · back within limit" if b.get("within_limit_on_latest_run") else "")
        )
        with st.expander(title, expanded=b["status"] != "CLOSED"):
            detail = client.breach(b["breach_id"])
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("First seen", b["first_date"], f"{b['first_utilisation']:.0%}")
            c2.metric("Latest", b["latest_date"], f"{b['latest_utilisation']:.0%}")
            c3.metric("Peak utilisation", f"{b['peak_utilisation']:.0%}")
            c4.metric("Runs breaching", b["consecutive_days"])
            st.caption(
                f"breach {b['breach_id']} · first run {b['first_run_id']} · latest run {b['latest_run_id']}"
            )
            hist = df(detail["actions"])
            if not hist.empty:
                st.dataframe(
                    hist[["at", "actor", "action", "comment", "escalated_to", "close_reason"]],
                    use_container_width=True,
                    hide_index=True,
                )
            if b["status"] != "CLOSED":
                actor = st.text_input("Acting as", value=b["owner"], key=f"actor_{b['breach_id']}")
                note = st.text_input("Comment", key=f"note_{b['breach_id']}")
                a1, a2, a3, a4 = st.columns(4)
                if b["status"] == "OPEN" and a1.button("Acknowledge", key=f"ack_{b['breach_id']}"):
                    act(client.acknowledge, b["breach_id"], actor, note)
                if b["status"] in ("OPEN", "ACKNOWLEDGED") and a2.button(
                    "Escalate", key=f"esc_{b['breach_id']}"
                ):
                    act(client.escalate, b["breach_id"], actor, None, note)
                if a3.button("Add comment", key=f"cmt_{b['breach_id']}"):
                    act(client.comment, b["breach_id"], actor, note)
                reason = a4.selectbox(
                    "Close reason",
                    ["RISK_REDUCED", "TEMPORARY_INCREASE_APPROVED", "LIMIT_RETIRED", "FALSE_POSITIVE"],
                    key=f"reason_{b['breach_id']}",
                )
                if a4.button("Close", key=f"close_{b['breach_id']}"):
                    act(client.close, b["breach_id"], actor, reason, note)
                with st.form(key=f"inc_{b['breach_id']}"):
                    st.markdown("**Request a temporary limit increase**")
                    lim_row = next(
                        (r for r in load("limits", run_id) if r["limit_id"] == b["limit_id"]), None
                    )
                    base = lim_row["base_amount"] if lim_row else 0.0
                    i1, i2, i3 = st.columns(3)
                    conc = b["limit_type"] == "CONCENTRATION"
                    new_amt = i1.number_input(
                        "New amount" + ("" if conc else " (m)"), value=float(base if conc else base / M) * 1.2
                    )
                    expires = i2.date_input("Expires on")
                    requester = i3.text_input("Requested by", value=b["owner"])
                    rationale = st.text_input("Rationale")
                    if st.form_submit_button("Submit request"):
                        act(
                            client.request_increase,
                            b["limit_id"],
                            new_amt if conc else new_amt * M,
                            str(expires),
                            requester,
                            rationale,
                            None,
                            b["breach_id"],
                        )
    st.subheader("Temporary limit increases")
    incs = client.increases()
    if not incs:
        st.caption("None requested.")
    for inc in incs:
        conc = inc["base_amount"] < 10
        fmt = (lambda x: f"{x:.2f}") if conc else (lambda x: money(x))
        st.markdown(
            f"**{inc['status']}** · {inc['limit_id']} · {fmt(inc['base_amount'])} → {fmt(inc['new_amount'])} "
            f"(+{inc['increase_pct']:.0%}) · until {inc['expires_on']} · "
            f"requested by {inc['requested_by']} · "
            f"approvers: {', '.join(inc['allowed_approvers'])}"
        )
        st.caption(
            inc["rationale"]
            + (
                f" · decided by {inc['decided_by']}: {inc['decision_comment']}"
                if inc.get("decided_by")
                else ""
            )
        )
        if inc["status"] == "REQUESTED":
            d1, d2, d3 = st.columns([2, 1, 1])
            approver = d1.selectbox(
                "Deciding as",
                inc["allowed_approvers"] + ["Head of Desk (not allowed)"],
                key=f"appr_{inc['increase_id']}",
            )
            if d2.button("Approve", key=f"ok_{inc['increase_id']}"):
                act(client.decide_increase, inc["increase_id"], approver, True, "")
            if d3.button("Reject", key=f"no_{inc['increase_id']}"):
                act(client.decide_increase, inc["increase_id"], approver, False, "")
    st.caption(
        "Approval matrix: desk and book limits need Head of Market Risk or CRO; firm and business "
        "limits need the CRO; increases above 25% always need the CRO; requesters cannot approve "
        "their own request (MR-008)."
    )

elif page == "Concentration & liquidity":
    header("Concentration and liquidity")
    conc = client.concentration(run_id)
    liq = client.liquidity(run_id)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("VaR", money(liq["var"], digits=2))
    c2.metric(
        "Liquidity-adjusted VaR",
        money(liq["liquidity_adjusted_var"], digits=2),
        money((liq["liquidity_adjusted_var"] or 0) - (liq["var"] or 0), digits=2),
    )
    c3.metric("Weighted liquidation horizon", f"{liq['horizon_days'] or 0:.1f} days")
    c4.metric("Flags", len(conc["flags"]) + len(liq["flags"]))
    for f in conc["flags"] + liq["flags"]:
        st.warning(f)
    st.subheader("Concentration by dimension")
    bd = df(conc["by_dimension"])
    if not bd.empty:
        show = bd[
            [
                "dimension",
                "basis",
                "groups",
                "hhi",
                "effective_number",
                "top1_share",
                "top5_share",
                "top10_share",
                "largest",
            ]
        ].copy()
        for c_ in ("top1_share", "top5_share", "top10_share"):
            show[c_] = (show[c_] * 100).round(0)
        st.dataframe(
            show.round(3).rename(
                columns={"top1_share": "top 1 %", "top5_share": "top 5 %", "top10_share": "top 10 %"}
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Largest VaR contributors")
    tp = df(conc["top_positions"])
    if not tp.empty:
        tp["pv"] = tp["pv"] / M
        tp["var_contribution"] = tp["var_contribution"] / M
        tp["share_of_var"] = (tp["share_of_var"] * 100).round(1)
        st.dataframe(
            tp.round(2).rename(
                columns={"pv": "pv (m)", "var_contribution": "component VaR (m)", "share_of_var": "share %"}
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Curve concentration (share of |DV01| by node)")
    tn = df(conc["tenor"])
    if not tn.empty:
        order = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
        piv = tn.pivot_table(
            index="currency", columns="bucket", values="share_of_abs_dv01", aggfunc="sum", fill_value=0.0
        )
        piv = piv.reindex(columns=[b for b in order if b in piv.columns])
        st.dataframe((piv * 100).round(0), use_container_width=True)
    st.subheader("Liquidation horizon")
    lb = df(liq["by_bucket"])
    if not lb.empty:
        lb["abs_pv"] = lb["abs_pv"] / M
        lb["share_of_abs_pv"] = (lb["share_of_abs_pv"] * 100).round(1)
        st.dataframe(
            lb.round(1).rename(columns={"abs_pv": "|PV| (m)", "share_of_abs_pv": "share %"}),
            use_container_width=True,
            hide_index=True,
        )
    ld = df(liq["by_desk"])
    if not ld.empty:
        ld["bidask_cost"] = ld["bidask_cost"] / M
        st.dataframe(
            ld.round(2).rename(columns={"bidask_cost": "bid-ask cost (m)"}),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Slowest positions to liquidate")
    sl = df(liq["slowest"])
    if not sl.empty:
        sl["pv"] = sl["pv"] / M
        st.dataframe(
            sl[
                [
                    "trade_id",
                    "desk_id",
                    "product_type",
                    "position",
                    "adv",
                    "days_to_liquidate",
                    "horizon_bucket",
                    "pv",
                ]
            ].round(1),
            use_container_width=True,
            hide_index=True,
        )
    st.caption(
        "Volume and bid-ask assumptions are synthetic (MR-013). Concentration uses component VaR "
        "where available (MR-012)."
    )

elif page == "Risk pack":
    header("Daily risk pack")
    st.markdown("Generates the HTML, PDF and Excel pack for the selected run from its stored results.")
    if st.button("Build the pack"):
        with st.spinner("Rendering…"):
            files = client.risk_pack(run_id)
        st.session_state["pack_files"] = files
    files = st.session_state.get("pack_files")
    if files:

        cols = st.columns(3)
        for col, key, mime in zip(
            cols,
            ("html", "pdf", "xlsx"),
            (
                "text/html",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            strict=True,
        ):
            path = files.get(key)
            if path and Path(path).exists():
                col.download_button(
                    f"Download {key.upper()}", Path(path).read_bytes(), file_name=Path(path).name, mime=mime
                )
            else:
                col.caption(f"{key.upper()} not generated")
        if files.get("html"):
            st.components.v1.html(Path(files["html"]).read_text(encoding="utf-8"), height=900, scrolling=True)

elif page == "Compare runs":
    header("Compare two runs")
    others = [r for r in labels if r != run_id]
    if not others:
        st.info("Only one run stored. Run a second business day to compare.")
        st.stop()
    other = st.selectbox("Compare against", others, format_func=labels.get)
    by = st.selectbox("VaR by", ["asset_class", "business_id", "desk_id", "book_id"])
    c = client.compare(other, run_id, by=by)
    st.caption(f"A = {labels[other]}   →   B = {labels[run_id]}")
    cols = st.columns(5)
    for col, key in zip(cols, ["var", "es", "worst_stress", "breaches", "pnl_total"], strict=False):
        h = c["headline"][key]
        is_money = key != "breaches"
        value = money(h["b"], digits=2) if is_money else h["b"]
        delta = None if h["change"] is None else (money(h["change"], digits=2) if is_money else h["change"])
        col.metric(key.replace("_", " "), value, delta)
    st.subheader(f"Component VaR by {by}")
    vb = df(c["var_by"])
    if not vb.empty:
        vb[["a", "b", "change"]] = vb[["a", "b", "change"]] / M
        st.dataframe(vb.round(2).sort_values("change"), use_container_width=True, hide_index=True)
    st.subheader("Limits that changed status or moved more than 10 points")
    lc = df(c["limit_changes"])
    if lc.empty:
        st.caption("None.")
    else:
        for col_ in ("utilisation_a", "utilisation_b"):
            lc[col_] = (lc[col_] * 100).round(0)
        st.dataframe(
            lc[["limit_id", "status_a", "status_b", "utilisation_a", "utilisation_b", "owner"]],
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Trades whose value moved most")
    st.caption(
        f"{c['trades_only_in_a']} trades only in A (unwound or matured), "
        f"{c['trades_only_in_b']} only in B (new)"
    )
    tt = df(c["top_trade_changes"])
    if not tt.empty:
        for col_ in ("pv_a", "pv_b", "pv_change"):
            tt[col_] = tt[col_] / M
        st.dataframe(
            tt[["trade_id", "desk_id", "product_type", "pv_a", "pv_b", "pv_change", "presence"]].round(2),
            use_container_width=True,
            hide_index=True,
        )

elif page == "Data quality":
    header("Can I trust today's run?")
    dq = df(load("dq", run_id))
    if dq.empty:
        st.success("No findings.")
    else:
        st.dataframe(
            dq.drop(columns=[c for c in ("affected_trade_ids",) if c in dq]),
            use_container_width=True,
            hide_index=True,
        )
        pick = st.selectbox("Finding", dq["code"] + " · " + dq["subject"])
        row = dq.iloc[list(dq["code"] + " · " + dq["subject"]).index(pick)]
        ids = [x for x in str(row.get("affected_trade_ids", "")).split(",") if x]
        st.write(
            f"{row['message']} — {len(ids)} affected trades" + (": " + ", ".join(ids[:25]) if ids else "")
        )

elif page == "Challenger":
    header("Independent challenger")
    rec = client.reconciliation(run_id)
    if rec is None:
        st.info("No reconciliation stored for this run. Generate a feed and reconcile it:")
        st.code("uv run novera vendor-feed\nuv run novera reconcile data/feeds/official_risk_<date>.csv")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Novera VaR", money(rec["novera_var"], digits=2))
        c2.metric(f"{rec['vendor']} VaR", money(rec["official_var"], digits=2))
        c3.metric(
            "Gap", money(rec["gap"], digits=2), f"{rec['gap_pct']:+.1%}" if rec.get("gap_pct") else None
        )
        c4.metric("Trades compared", rec["trades_compared"])
        st.caption(f"reconciliation {rec['recon_id']} · run {rec['run_id']} · {rec['business_date']}")
        st.subheader("Where the gap comes from")
        att = pd.Series(rec["attribution"], name="m") / M
        st.bar_chart(att)
        for f in rec["findings"]:
            st.markdown(f"- {f}")
        planted = (rec.get("meta") or {}).get("planted_differences")
        if planted:
            with st.expander("Planted differences in the simulated feed (demo only)"):
                for x in planted:
                    st.markdown(f"- {x}")
        st.subheader("By desk")
        bd = df(rec["by_desk"])
        if not bd.empty:
            for col_ in ("novera_var", "official_var", "gap"):
                bd[col_] = bd[col_] / M
            st.dataframe(bd.round(2), use_container_width=True, hide_index=True)
        st.subheader("Largest trade-level differences")
        ld = df(rec["largest_differences"])
        if not ld.empty:
            for col_ in (
                "pv",
                "official_pv",
                "pv_diff",
                "var_contribution",
                "official_var_contribution",
                "var_diff",
            ):
                if col_ in ld:
                    ld[col_] = ld[col_] / M
            st.dataframe(
                ld[
                    [
                        "trade_id",
                        "desk_id",
                        "product_type",
                        "presence",
                        "cause",
                        "pv",
                        "official_pv",
                        "var_contribution",
                        "official_var_contribution",
                        "var_diff",
                    ]
                ].round(3),
                use_container_width=True,
                hide_index=True,
            )

elif page == "Alerts & jobs":
    header("Alerts and scheduled jobs")
    st.subheader("Alerts")
    sev = st.multiselect("Severity", ["CRITICAL", "WARNING", "INFO"], default=["CRITICAL", "WARNING"])
    al = df(client.alerts(limit=300))
    if al.empty:
        st.caption("No alerts stored yet.")
    else:
        al = al[al["severity"].isin(sev)]
        for _, a in al.head(40).iterrows():
            fn = {"CRITICAL": st.error, "WARNING": st.warning, "INFO": st.info}[a["severity"]]
            fn(
                f"**{a['title']}** · {a['business_date']} · {a['status']} · "
                f"to {', '.join(a['recipients'])}\n\n"
                f"{a['body']}"
            )
    st.subheader("Scheduled jobs")
    jb = df(client.jobs())
    if jb.empty:
        st.caption("No scheduled jobs yet. Run `uv run novera schedule --once` or `uv run novera schedule`.")
    else:
        st.dataframe(
            jb[["started_at", "action", "business_date", "status", "attempts", "run_id", "notes"]],
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Market-data provenance")
    pv = df(client.provenance())
    if pv.empty:
        st.caption("All history is synthetic. Run `uv run novera fetch` to load real series.")
    else:
        st.dataframe(pv, use_container_width=True, hide_index=True)

elif page == "Runs & audit":
    header("Runs and audit trail")
    rdf = df(runs)
    st.dataframe(
        rdf[
            [
                "business_date",
                "run_id",
                "status",
                "verdict",
                "portfolio_snapshot_id",
                "market_snapshot_id",
                "config_hash",
                "started_at",
                "finished_at",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.subheader("Timings (s)")
    st.json(summary["timings"])
    st.subheader("Audit events")
    st.dataframe(df(client.audit(limit=200)), use_container_width=True, hide_index=True)
