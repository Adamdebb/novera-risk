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

FIRMS = {"Global Macro Bank": settings.db_path, "Meridian Multi-Strategy Fund": settings.fund_db_path}
_available = {k: v for k, v in FIRMS.items() if Path(v).exists()} or {"Global Macro Bank": settings.db_path}
firm_name = st.sidebar.selectbox("Firm", list(_available), key="firm")
is_fund = "Fund" in firm_name
client = make_client(settings, db_path=_available[firm_name])

M = 1e6


def money(x: float | None, unit: str = "m", digits: int = 1) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x / M:,.{digits}f}{unit}"


def df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def load_runs(firm: str) -> list[dict]:
    return client.runs(30)


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, run_id: str, firm: str = "", **kw):
    return getattr(client, name)(run_id=run_id, **kw)


# --- sidebar: run selection -----------------------------------------------------------
runs = load_runs(firm_name)
if not runs:
    st.title(f"{settings.platform_name}")
    st.warning("No completed run found. Run `novera simulate` then `novera run eod`.")
    st.stop()
labels = {
    r["run_id"]: f"{r['business_date']}  {r['run_id']}  [{r['verdict']}]"
    + (f"  · rerun of {r['summary'].get('rerun', {}).get('stage', '?')}" if r["run_type"] == "RERUN" else "")
    for r in runs
}
with st.sidebar:
    st.markdown(f"## {settings.platform_name}")
    st.caption(f"{settings.platform_tagline} · {firm_name}")
    default_run = next((i for i, r in enumerate(runs) if r["run_type"] == "EOD"), 0)  # not a re-run
    run_id = st.selectbox("Run", list(labels), index=default_run, format_func=labels.get)
    pages = ["Overview", "Analyst", "Drill-down", "Trade extract", "VaR", "Stress", "Stress library"]
    pages += ["Limit management"]
    pages += ["Breaches", "Sign-off"]
    pages += ["Counterparty"]
    pages += ["Fund"] if is_fund else ["Capital"]
    pages += [
        "P&L explain",
        "Data quality",
        "Concentration & liquidity",
        "Compare runs",
        "Challenger",
        "Risk pack",
        "Agents",
        "Portfolio Lab",
        "Alerts & jobs",
        "Runs & audit",
        "Market data",
        "Reference data",
        "Admin",
    ]
    page = st.radio("View", pages)
    st.caption("Every figure is read from the stored run. Nothing is computed on this page.")

summary = load("summary", run_id)
sm = summary["summary"]
ccy = summary["reporting_currency"]
verdict_colour = {"GREEN": "🟢", "AMBER": "🟠", "RED": "🔴"}.get(summary["verdict"], "⚪")


RELEASE_BADGE = {"RELEASED": "✅", "BLOCKED": "⛔", "PENDING": "⏳", "NO_POLICY": "—"}


def header(title: str) -> None:
    st.title(title)
    try:
        rel = load("signoff_status", run_id)
        release = f" · release {RELEASE_BADGE.get(rel['release_status'], '')} {rel['release_status']}"
    except Exception:  # noqa: BLE001 - a run type outside sign-off simply shows no release
        release = ""
    st.caption(
        f"Business date {summary['business_date']} · run {run_id} · verdict {verdict_colour} "
        f"{summary['verdict']}{release} · portfolio {summary['portfolio_snapshot_id']} · market "
        f"{summary['market_snapshot_id']} · models "
        f"{', '.join(f'{k} {v}' for k, v in summary['model_versions'].items())}"
    )


# --- pages ------------------------------------------------------------------------------
if page == "Overview":
    header("Global market risk")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(f"VaR {sm.get('var_confidence') or 0.99:.0%} 1d", money(sm["var"], digits=2))
    c2.metric(f"ES {sm.get('es_confidence') or 0.975:.1%}".replace(".0%", "%"), money(sm["es"], digits=2))
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

elif page == "Analyst":
    header(f"{settings.platform_name} Analyst")
    prov = client.analyst_provider()
    if prov["provider"] == "scripted":
        st.info(
            "Running the scripted provider: answers are templated from stored numbers. Set "
            "GEMINI_API_KEY in .env for the free Gemini tier, or ANTHROPIC_API_KEY for Claude."
        )
    else:
        chain = prov.get("chain") or []
        st.caption(
            f"Provider {prov['provider']} · model {prov['model']}"
            + (f" · fallback order {' → '.join(chain)}" if len(chain) > 1 else "")
            + " · answers cite run ids and are stored with their tool calls."
        )
    if "analyst_chat" not in st.session_state:
        st.session_state.analyst_chat = []
        st.session_state.analyst_session = f"ui_{run_id}"
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
    for turn in st.session_state.analyst_chat:
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
        st.session_state.analyst_chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.spinner("Reading the run…"):
            if question.lower().startswith("draft the morning commentary"):
                ans = client.commentary(run_id)
            else:
                ans = client.ask(question, run_id=run_id, session_id=st.session_state.analyst_session)
            st.markdown(ans["answer"])
        st.session_state.analyst_chat.append(
            {
                "role": "assistant",
                "content": ans["answer"],
                "tool_calls": ans["tool_calls"],
                "seconds": ans["seconds"],
                "run_ids": ans["run_ids_cited"],
            }
        )
        st.rerun()

    with st.expander("Same tools from outside: MCP server (AI-004)"):
        st.write(
            "External MCP clients (Claude Desktop, Claude Code, a firm's own agent) reach these same tools "
            "through `novera mcp`. The server never calls a model; the client's model configuration decides "
            "where questions go. Every external call lands in the audit trail below."
        )
        st.code(
            f"claude mcp add novera -- uv run --directory {settings.data_dir.resolve().parent} novera mcp"
            + (" --fund" if is_fund else ""),
            language="bash",
        )
        mcp_calls = [e for e in client.audit(limit=500) if e.get("event_type") == "MCP_TOOL_CALL"][:50]
        if mcp_calls:
            rows = []
            for e in mcp_calls:
                p = e.get("payload") or {}
                if isinstance(p, str):
                    p = json.loads(p)
                rows.append(
                    {
                        "at": e["at"],
                        "tool": e["subject"],
                        "arguments": json.dumps(p.get("arguments", {}))[:80],
                        "seconds": p.get("seconds"),
                        "error": p.get("is_error"),
                    }
                )
            st.dataframe(df(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No external MCP calls recorded yet.")

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
    method_labels = {
        "historical_full_revaluation": "Historical full revaluation",
        "delta_gamma_vega": "Delta-gamma-vega challenger",
        "monte_carlo_delta_gamma_vega": "Monte Carlo (delta-gamma-vega)",
        "historical_weighted_full_revaluation": "Weighted historical full revaluation",
        "historical_weighted_delta_gamma_vega": "Weighted historical delta-gamma-vega",
    }
    # Runs stored before the VaR setup existed carry the method columns only (the shared
    # table gained the measure columns later, so they read as empty on those runs).
    legacy = "measure_id" not in vs.columns or vs["measure_id"].isna().all()
    if legacy:
        vs = vs.assign(
            measure_id=vs["method"],
            goal="",
            metric="VAR",
            label=vs["method"].map(lambda m: method_labels.get(m, m)),
            value=vs["var"],
            limit_type=None,
        )
    headline_id = sm.get("var_measure_id") or (vs["measure_id"].iloc[0] if len(vs) else None)

    def measure_cards(rows: pd.DataFrame, title: str) -> None:
        if rows.empty:
            return
        st.markdown(f"**{title}**")
        for col, (_, r) in zip(st.columns(min(max(len(rows), 1), 4)), rows.iterrows(), strict=False):
            if r["metric"] == "ES":
                note = f"VaR {money(r['var'], digits=2)} on the same scenarios"
            else:
                note = f"ES {money(r['es'], digits=2)} · 10d {money(r['var_scaled'])}"
            note += f" · {int(r['scenarios'])} scenarios"
            if r["measure_id"] == headline_id:
                note += " · headline"
            col.metric(r["label"], money(r["value"], digits=2), note, delta_color="off")

    if legacy:
        measure_cards(vs, "Methods")
    else:
        measure_cards(vs[vs["goal"] == "LIMIT"], "Feed the limits")
        measure_cards(vs[vs["goal"] != "LIMIT"], "For information")
        table = vs.assign(
            **{
                "value (m)": vs["value"] / M,
                "VaR (m)": vs["var"] / M,
                "ES (m)": vs["es"] / M,
                "window": vs.apply(
                    lambda r: (
                        f"{r['window_start']} to {r['window_end']}"
                        if isinstance(r.get("window_start"), str)
                        else f"{int(r['window_days'])} days"
                    ),
                    axis=1,
                ),
            }
        )
        with st.expander("Every measure of the run's VaR setup"):
            st.dataframe(
                table[
                    [
                        "goal",
                        "label",
                        "value (m)",
                        "VaR (m)",
                        "ES (m)",
                        "window",
                        "decay",
                        "scenarios",
                        "var_scenario_date",
                        "limit_type",
                        "seconds",
                    ]
                ].round(2),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "The LIMIT measure of each metric feeds the limits of that type (VaR, expected "
                "shortfall, stressed VaR); INFORMATION measures are reported only. The matrix is "
                "set on the Admin page (OPS-004) and recorded in the run's configuration."
            )

    by = st.selectbox("Component VaR by", ["asset_class", "business_id", "desk_id", "book_id", "currency"])
    if legacy:
        prim = df(load("var_by", run_id, by=by)).set_index(by)
        chal = df(load("var_by", run_id, by=by, method="delta_gamma_vega"))
        comp = pd.DataFrame({"full revaluation (m)": prim["component_var"] / M})
        if not chal.empty:
            comp["delta-gamma-vega (m)"] = chal.set_index(by)["component_var"] / M
        if "monte_carlo_delta_gamma_vega" in set(vs["method"]):
            mc = df(load("var_by", run_id, by=by, method="monte_carlo_delta_gamma_vega"))
            if not mc.empty:
                comp["monte carlo (m)"] = mc.set_index(by)["component_var"] / M
    else:
        cols = {}
        for _, r in vs.iterrows():
            part = df(load("var_by", run_id, by=by, measure_id=r["measure_id"]))
            if not part.empty:
                key = "component_es" if r["metric"] == "ES" else "component_var"
                cols[f"{r['label']} (m)"] = part.set_index(by)[key] / M
        comp = pd.DataFrame(cols)
    comp = comp.fillna(0.0)
    head_col = next(
        (c for c in comp.columns if "full revaluation" in c and "weighted" not in c.lower()), None
    )
    chal_col = next((c for c in comp.columns if "delta-gamma-vega" in c and "Monte" not in c), None)
    if head_col and chal_col:
        comp["challenger difference (m)"] = comp[chal_col] - comp[head_col]
    st.dataframe(comp.round(2), use_container_width=True)
    st.caption(
        "Where the challenger disagrees, the gap is convexity, cross effects and surface shape "
        "that sensitivities miss (MR-004). Monte Carlo values the same sensitivities on 10,000 "
        "Gaussian factor moves drawn from the window's covariance (MR-010): its distance from "
        "the challenger is the tail shape of the history, not the pricing approximation. "
        "Weighted measures (MR-015) let the newest scenarios dominate; a stressed VaR (MR-016) "
        "replays a fixed window."
    )
    sc = df(load("var_scenarios", run_id))
    if not sc.empty:
        sc["scenario_date"] = pd.to_datetime(sc["scenario_date"])
        st.subheader("Scenario P&L distribution")
        series = [c for c in ("portfolio_pnl", "challenger_pnl") if c in sc.columns and sc[c].notna().any()]
        st.line_chart(sc.set_index("scenario_date")[series] / M)
        worst = sc.nsmallest(10, "portfolio_pnl")
        shown = worst.assign(**{"portfolio pnl (m)": worst["portfolio_pnl"] / M})
        cols_ = ["scenario_date", "portfolio pnl (m)"]
        if "challenger_pnl" in series:
            shown["challenger (m)"] = worst["challenger_pnl"] / M
            cols_.append("challenger (m)")
        st.dataframe(shown[cols_], hide_index=True)
    if not legacy:
        weighted = vs[vs["decay"].notna()] if "decay" in vs.columns else vs.iloc[0:0]
        if not weighted.empty:
            st.subheader("Scenario weights of the weighted measures")
            ws = df(load("var_measure_scenarios", run_id, measure_id=weighted["measure_id"].iloc[0]))
            if not ws.empty:
                ws["scenario_date"] = pd.to_datetime(ws["scenario_date"])
                st.line_chart(ws.set_index("scenario_date")[["weight"]])
                st.caption(
                    f"{weighted['label'].iloc[0]}: the newest scenario weighs "
                    f"{ws['weight'].max():.2%}, the oldest {ws['weight'].min():.4%} (MR-015)."
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

elif page == "Stress library":
    header("Stress library")
    st.caption(
        "Every stress scenario the platform knows, by category, with the shocks it applies. "
        "Hypothetical shocks are rules; historical windows show the realised move of headline "
        "factors in the stored history (MR-005)."
    )

    @st.cache_data(ttl=60, show_spinner=False)
    def load_stress_library(firm: str) -> dict:
        return client.stress_library()

    lib = load_stress_library(firm_name)
    hist, tot = lib["history"], lib["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scenarios", tot["scenarios"], f"{tot['in_daily_run']} in the daily run")
    c2.metric("Stored history", f"{hist['days']:,} days", f"{hist['start']} to {hist['end']}")
    c3.metric("Real factors in store", f"{hist['real_factors']:,} / {hist['factors']:,}")
    c4.metric("Real crises replayable", tot["replayable_real"])
    status_names = {
        "IN_RUN": "🟢 in the daily run",
        "REAL": "🟢 replayable on real data",
        "PARTLY_REAL": "🟡 partly real data",
        "SYNTHETIC_WINDOW": "⚪ window covered, synthetic data only",
        "NOT_COVERED": "⚫ outside the stored history",
    }
    for cat in lib["categories"]:
        st.subheader(f"{cat['title']} · {cat['count']}")
        st.caption(cat["description"])
        for sc in cat["scenarios"]:
            window = f" · {sc['window_start']} to {sc['window_end']}" if sc["window_start"] else ""
            with st.expander(f"{sc['name']} · {status_names.get(sc['status'], sc['status'])}{window}"):
                st.markdown(sc["description"])
                if sc.get("note"):
                    st.caption(sc["note"])
                shocks = df(sc["shocks"])
                if shocks.empty:
                    st.caption("No shocks to show: the window is outside the stored history.")
                else:
                    label = (
                        "Shock rule"
                        if cat["category"] == "HYPOTHETICAL"
                        else "Realised move of headline factors"
                    )
                    st.markdown(f"**{label}**")
                    cols = ["family", "target", "size", "unit"]
                    if cat["category"] == "HYPOTHETICAL":
                        cols.append("tenors")
                        shocks["tenors"] = shocks["tenors"].fillna("all")
                    st.dataframe(shocks[cols].round(1), use_container_width=True, hide_index=True)

elif page == "Admin":
    header("Administration")
    st.caption(
        "Operational configurations for risk control. Every action names an actor and is written "
        "to the audit trail. Nothing here edits a stored run."
    )
    config = st.selectbox("Configuration", ["VaR measures", "Sign-off policy", "Runs"])
    if config == "VaR measures":
        from novera.api.errors import ApiError

        st.markdown(
            "The VaR matrix: which measures the firm produces every day, which of them feed the "
            "limits and which are for information (OPS-004). One LIMIT row per metric: the VaR "
            "row feeds VaR limits, the ES row expected-shortfall limits, the stressed-VaR row "
            "stressed-VaR limits. The next EOD run produces the saved matrix and records it in "
            "its configuration; stored runs are not changed."
        )
        vsu = client.var_setup()
        opts = vsu["options"]
        last = next((m for m in vsu["measures"] if m.get("updated_by")), None)
        st.caption(
            f"Current setup ({vsu['source']}): headline {vsu['headline']} · stored history "
            f"{vsu['history_start']} to {vsu['history_end']}"
            + (f" · last changed by {last['updated_by']} at {str(last['updated_at'])[:19]}" if last else "")
        )
        feeds = {m["limit_type"]: m["label"] for m in vsu["measures"] if m["limit_type"] and m["enabled"]}
        fallback = "no measure (VaR and ES limits fall back to the headline VaR)"
        st.markdown(
            "\n".join(
                f"- **{name} limits** ← {feeds.get(lt, fallback)}"
                for lt, name in (
                    ("VAR", "VaR"),
                    ("EXPECTED_SHORTFALL", "Expected-shortfall"),
                    ("STRESSED_VAR", "Stressed-VaR"),
                )
            )
        )
        editor_cols = [
            "goal",
            "metric",
            "confidence",
            "shocks",
            "compute",
            "window_years",
            "window_start",
            "window_end",
            "decay",
            "enabled",
        ]

        def _rows(ms: list[dict]) -> pd.DataFrame:
            return pd.DataFrame([{c: m.get(c) for c in editor_cols} for m in ms], columns=editor_cols)

        if "var_setup_rows" not in st.session_state:
            st.session_state["var_setup_rows"] = _rows(vsu["measures"])
        b1, b2, b3 = st.columns(3)
        if b1.button("Load the bank template"):
            st.session_state["var_setup_rows"] = _rows(vsu["templates"]["bank"])
            st.session_state.pop("var_setup_editor", None)
        if b2.button("Load the hedge-fund template"):
            st.session_state["var_setup_rows"] = _rows(vsu["templates"]["hedge_fund"])
            st.session_state.pop("var_setup_editor", None)
        if b3.button("Reload the current setup"):
            st.session_state["var_setup_rows"] = _rows(vsu["measures"])
            st.session_state.pop("var_setup_editor", None)
        if vsu.get("stress_window"):
            st.caption(
                f"Bank template stressed window: {vsu['stress_window'][0]} to {vsu['stress_window'][1]}, "
                "the most volatile year of the equity index in the stored history. Edit the dates to "
                "replay another period."
            )
        edited = st.data_editor(
            st.session_state["var_setup_rows"],
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key="var_setup_editor",
            column_config={
                "goal": st.column_config.SelectboxColumn("Goal", options=opts["goals"], required=True),
                "metric": st.column_config.SelectboxColumn("Metric", options=opts["metrics"], required=True),
                "confidence": st.column_config.NumberColumn(
                    "Confidence", min_value=0.5, max_value=0.9999, step=0.005, format="%.3f", required=True
                ),
                "shocks": st.column_config.SelectboxColumn("Shocks", options=opts["shocks"], required=True),
                "compute": st.column_config.SelectboxColumn(
                    "Compute", options=opts["computes"], required=True
                ),
                "window_years": st.column_config.NumberColumn("Window (years)", min_value=0.1, step=0.5),
                "window_start": st.column_config.TextColumn("Window start", help="YYYY-MM-DD, fixed window"),
                "window_end": st.column_config.TextColumn("Window end", help="YYYY-MM-DD"),
                "decay": st.column_config.NumberColumn(
                    "Lambda", min_value=0.5, max_value=0.999, step=0.01, format="%.3f"
                ),
                "enabled": st.column_config.CheckboxColumn("Produce daily", default=True),
            },
        )
        with st.form("var_setup_save"):
            c1, c2 = st.columns(2)
            actor = c1.text_input("Acting as", value="Head of Market Risk", key="vsu_actor")
            comment = c2.text_input("Comment", key="vsu_comment")
            go = st.form_submit_button("Save the VaR setup")
        if go:
            measures = [
                {k: (None if pd.isna(v) else v) for k, v in row.items()}
                for row in edited.to_dict("records")
                if not pd.isna(row.get("metric")) and row.get("metric")
            ]
            try:
                res = client.set_var_setup(actor.strip(), measures, comment.strip())
                st.success(
                    f"Setup saved: {len(res['measures'])} measures, headline {res['headline']}. "
                    "The next EOD run produces them; a partial re-run of the VaR stage on a stored run "
                    "keeps that run's own matrix."
                )
                st.session_state["var_setup_rows"] = _rows(res["measures"])
                st.session_state.pop("var_setup_editor", None)
                st.cache_data.clear()
            except ApiError as e:
                st.error(str(e))
        with st.expander("What each column means"):
            st.markdown(
                "- **Goal**: LIMIT feeds the limits of the metric's type; INFORMATION is reported only.\n"
                "- **Metric**: VaR, ES (expected shortfall at the confidence given) or stressed VaR, "
                "which needs a fixed window.\n"
                "- **Shocks**: historical (equal weights, MR-002), weighted historical (exponential "
                "decay with the lambda given, MR-015) or Monte Carlo (Gaussian draws from the window's "
                "covariance, MR-010, on the sensitivities only).\n"
                "- **Compute**: full revaluation of every trade, or the delta-gamma-vega expansion of "
                "the sensitivities (MR-004).\n"
                "- **Window**: years back from the valuation date (250 business days a year), or a "
                "fixed start and end date inside the stored history (MR-016).\n"
                "- Measures sharing scenarios and compute share one P&L matrix, so an ES row next to "
                "a VaR row costs nothing."
            )
    elif config == "Sign-off policy":
        from novera.api.errors import ApiError

        st.markdown(
            "Choose the metrics that must be signed before a run is released (OPS-003). The policy "
            "applies to every run not yet released; a change is audited with the sets before and after."
        )
        pol = client.signoff_policy()
        face = "fund" if is_fund else "bank"
        metrics = [m for m in pol["metrics"] if face in m["faces"]]
        required_here = [m["metric_id"] for m in metrics if m["required"]]
        st.caption(
            f"Current policy ({pol['source']}): {', '.join(required_here) or 'nothing required'}"
            + (
                f" · last changed by {metrics[0]['updated_by']} at {str(metrics[0]['updated_at'])[:19]}"
                if metrics and metrics[0]["updated_by"]
                else ""
            )
        )
        with st.form("signoff_policy"):
            chosen = []
            cols = st.columns(2)
            for i, m in enumerate(metrics):
                if cols[i % 2].checkbox(
                    f"{m['title']} · {m['record']} · signer {m['signer']}",
                    value=m["required"],
                    key=f"pol_{m['metric_id']}",
                ):
                    chosen.append(m["metric_id"])
            c1, c2 = st.columns(2)
            actor = c1.text_input("Acting as", value="Chief Risk Officer", key="pol_actor")
            comment = c2.text_input("Comment", key="pol_comment")
            go = st.form_submit_button("Save policy")
        if go:
            try:
                res = client.set_signoff_policy(actor.strip(), chosen, comment.strip())
                st.success(f"Policy saved: {', '.join(res['required']) or 'nothing required'}.")
                st.cache_data.clear()
            except ApiError as e:
                st.error(str(e))
    elif config == "Runs":
        from novera.api.errors import ApiError

        mode = st.radio(
            "What to run",
            ["Full end-of-day run", "One stage of a stored run"],
            horizontal=True,
            help="A full run is the official end-of-day: a new EOD run that becomes the latest, "
            "synchronises breaches and raises alerts. A partial re-run recomputes one stage of a "
            "stored run into a RERUN run and touches nothing else (OPS-002).",
        )
        if mode == "Full end-of-day run":
            ro = client.run_options()
            engines = ["valuation", "sensitivities", "VaR", "stress", "limits", "P&L explain", "data quality"]
            if ro["regulatory_enabled"] and not is_fund:
                engines.append("regulatory capital")
            if ro["counterparty_enabled"] and not is_fund:
                engines.append("counterparty exposure")
            if is_fund:
                engines.append("fund metrics")
            st.markdown(
                f"Run the whole end-of-day process for **{firm_name}** now, exactly as the scheduler "
                f"would at {settings.eod_time} (OPS-001): {', '.join(engines)}, then breach "
                "synchronisation and alerts. The run is recorded as a job with the person who "
                "launched it and becomes the latest EOD run."
            )
            if is_fund:
                duration = "under a minute for the demo fund"
            elif ro["counterparty_enabled"]:
                duration = (
                    "about four and a half minutes for the demo bank, three of them in the counterparty "
                    "engine (set NOVERA_EXPOSURE_ENABLED=false for a one-minute run)"
                )
            else:
                duration = "about a minute for the demo bank with the counterparty engine off"
            st.caption(
                f"Latest market snapshot {ro['latest_snapshot_date']} · latest run "
                f"{ro['latest_run_date'] or 'none'} · takes {duration}. The page waits for it."
            )
            with st.form("manual_run"):
                c1, c2 = st.columns(2)
                bd = c1.selectbox(
                    "Business date",
                    ro["snapshot_dates"],
                    help="A stored market snapshot. The portfolio snapshot on or before it is used.",
                )
                advance = c2.checkbox(
                    "Advance the simulated market by one business day first",
                    value=False,
                    help="Simulation only: bootstraps the next day's market from history and books a "
                    "day of new business from the firm's own template, as the scheduler does. Applies "
                    "when the latest day already has a completed run; the business date above is then "
                    "ignored.",
                )
                c3, c4 = st.columns(2)
                actor = c3.text_input("Actor", value="Risk Control", key="manual_run_actor")
                reason = c4.text_input(
                    "Reason",
                    placeholder="e.g. late trade booking on the credit desk",
                    key="manual_run_reason",
                )
                go = st.form_submit_button(f"Run the full end-of-day for {firm_name}")
            if go:
                if not actor.strip():
                    st.error("Name the actor: every manual run is audited.")
                else:
                    try:
                        with st.spinner(f"Running the end-of-day for {firm_name}, {duration}..."):
                            res = client.run_full(actor.strip(), reason.strip(), bd, advance)
                    except ApiError as e:
                        st.error(str(e))
                        res = None
                    if res is not None:
                        for n in res.get("notes", []):
                            st.caption(n)
                        if res["status"] == "COMPLETED" and res.get("run"):
                            r = res["run"]
                            sm = r["summary"]
                            secs = sum(r.get("timings", {}).values())
                            st.success(
                                f"Run {r['run_id']} for {r['business_date']} completed in {secs:.0f}s: "
                                f"verdict {r['verdict']}, VaR {money(sm.get('var'))}, "
                                f"{sm.get('breaches', 0)} breach and {sm.get('warnings', 0)} warning."
                            )
                            load_runs.clear()
                            st.cache_data.clear()
                            st.caption("It is now the latest run; select it in the sidebar to browse it.")
                        else:
                            first = (res.get("error") or "unknown error").splitlines()[0]
                            st.error(
                                f"Run failed: {first}. The job {res['job_id']} is recorded with the error."
                            )
                    ro = client.run_options()
            st.subheader("Manual runs so far")
            if ro["jobs"]:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "started_at": str(j["started_at"])[:19],
                                "business_date": j["business_date"],
                                "status": j["status"],
                                "run_id": j["run_id"],
                                "launched": "; ".join(j.get("notes", [])),
                                "error": (j.get("error") or "").splitlines()[0] if j.get("error") else "",
                            }
                            for j in ro["jobs"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No manual run yet. Scheduled runs are listed under Alerts & jobs.")
        else:
            st.markdown(
                "Re-run one stage of the EOD workflow on a stored run. The result is a new run of type "
                "RERUN with the parent's results copied and only that stage recomputed from the same "
                "snapshots (OPS-002). Stages downstream of it are copied, not recomputed, and listed as "
                "such. A re-run never raises or escalates breaches, sends alerts, or becomes the latest "
                "EOD run."
            )
            opts = client.rerun_options()
            face = "fund" if is_fund else "bank"
            stages = {s["name"]: s for s in opts["stages"] if face in s["faces"]}
            with st.form("rerun"):
                c1, c2 = st.columns(2)
                parent = c1.selectbox(
                    "Run to re-run", list(labels), index=list(labels).index(run_id), format_func=labels.get
                )
                stage = c2.selectbox("Stage", list(stages), format_func=lambda n: stages[n]["title"])
                c3, c4 = st.columns(2)
                actor = c3.text_input("Actor", value="Risk Control")
                reason = c4.text_input("Reason", placeholder="e.g. market data correction on the USD 7Y node")
                go = st.form_submit_button("Run the stage")
            spec = stages[stage]
            st.caption(
                f"{spec['description']} Replaces: {', '.join(spec['tables'])}. Not recomputed: "
                f"{', '.join(spec['dependents']) or 'nothing depends on it'}."
            )
            if go:
                if not actor.strip():
                    st.error("Name the actor: every re-run is audited.")
                else:
                    with st.spinner(f"Re-running {spec['title'].lower()} on {parent}..."):
                        res = client.rerun_stage(parent, stage, actor.strip(), reason.strip())
                    info = res["rerun"]
                    st.success(
                        f"Re-run {res['run_id']} completed in {info['seconds']:.0f}s "
                        f"({info['copied_tables']} tables copied from {info['parent_run_id']})."
                    )
                    if info["changed"]:
                        st.markdown("**Summary values that changed**")
                        st.dataframe(
                            pd.DataFrame(
                                [
                                    {"measure": k, "before": str(v["before"]), "after": str(v["after"])}
                                    for k, v in info["changed"].items()
                                ]
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.info(
                            "The stage reproduced the parent's numbers exactly (same inputs, same results)."
                        )
                    if info["stale_stages"]:
                        st.caption(
                            f"Copied from the parent, not recomputed: {', '.join(info['stale_stages'])}."
                        )
                    load_runs.clear()
                    st.caption("Select the new run in the sidebar to browse it.")
                    opts = client.rerun_options()  # the list below includes the run just made
            st.subheader("Re-runs so far")
            if opts["reruns"]:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "run_id": r["run_id"],
                                "business_date": r["business_date"],
                                "stage": r["rerun"].get("stage"),
                                "parent": r["rerun"].get("parent_run_id"),
                                "actor": r["rerun"].get("actor"),
                                "reason": r["rerun"].get("reason"),
                                "status": r["status"],
                                "changed": ", ".join(r["rerun"].get("changed", {})) or "nothing",
                                "seconds": round(r["rerun"].get("seconds", 0) or 0),
                            }
                            for r in opts["reruns"]
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No re-run yet.")

elif page == "Sign-off":
    header("Sign-off and release")
    from novera.api.errors import ApiError

    st.caption(
        "Named people sign each metric the policy requires; the run is released when every required "
        "metric is signed. The value seen is frozen with the signature and every action is audited. "
        "Nothing here edits the run (OPS-003)."
    )
    so = client.signoff_status(run_id)

    def fmt_value(v: dict) -> str:
        parts = []
        for k, x in v.items():
            if isinstance(x, dict):
                inner = {
                    kk: xx
                    for kk, xx in x.items()
                    if isinstance(xx, (int, float)) and not isinstance(xx, bool)
                }
                parts.append(
                    f"{k}: "
                    + ", ".join(
                        f"{kk} {money(xx, digits=2) if abs(xx) >= 1e5 else xx}"
                        for kk, xx in list(inner.items())[:4]
                    )
                )
            elif isinstance(x, float) and abs(x) >= 1e5:
                parts.append(f"{k} {money(x, digits=2)}")
            elif x is not None:
                parts.append(f"{k} {x}")
        return " · ".join(parts)

    badge = RELEASE_BADGE.get(so["release_status"], "")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Release", f"{badge} {so['release_status']}")
    c2.metric("Required metrics signed", f"{so['signed_required']} / {so['required_total']}")
    c3.metric("Verdict", f"{verdict_colour} {so['verdict']}", "override" if so["override"] else None)
    c4.metric("Released by", so["released_by"] or "—", (so["released_at"] or "")[:19] or None)
    status_icon = {"SIGNED": "🟢 signed", "REJECTED": "🔴 rejected", "PENDING": "⚪ pending"}
    rows = [
        {
            "metric": m["title"],
            "id": m["metric_id"],
            "required": "required" if m["required"] else "optional",
            "status": status_icon.get(m["status"], m["status"]),
            "actor": m["actor"] or "",
            "at": (m["at"] or "")[:19],
            "comment": m["comment"] or "",
            "value": fmt_value(m["value"]),
            "expected signer": m["signer"],
        }
        for m in sorted(so["metrics"], key=lambda m: (not m["required"], m["metric_id"]))
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    by_id = {m["metric_id"]: m for m in so["metrics"]}
    order = [r["id"] for r in rows]
    with st.form("signoff"):
        c1, c2 = st.columns([2, 1])
        metric = c1.selectbox(
            "Metric",
            order,
            format_func=lambda k: (
                f"{by_id[k]['title']} ({'required' if by_id[k]['required'] else 'optional'})"
            ),
        )
        action = c2.radio("Action", ["Sign", "Reject"], horizontal=True)
        c3, c4 = st.columns(2)
        actor = c3.text_input("Acting as", value="Head of Market Risk")
        comment = c4.text_input("Comment", placeholder="mandatory for a rejection or a RED override")
        go = st.form_submit_button("Record")
    if go:
        try:
            fn = client.sign_metric if action == "Sign" else client.reject_metric
            res = fn(run_id, metric, actor.strip(), comment.strip())
            st.success(f"{metric} {action.lower()}ed by {actor}; release {res['release_status']}.")
            st.cache_data.clear()
            st.rerun()
        except ApiError as e:
            st.error(str(e))

    st.subheader("Sign-off trail")
    trail = [
        e
        for e in client.audit(subject=run_id, limit=200)
        if str(e.get("event_type", "")).startswith(("METRIC_", "RUN_RELEASE"))
    ]
    if trail:
        st.dataframe(
            pd.DataFrame(trail)[["at", "actor", "event_type", "payload"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No sign-off action on this run yet.")

    st.subheader("Release queue")
    q = client.signoff_queue(20)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "business_date": r["business_date"],
                    "run_id": r["run_id"],
                    "verdict": r["verdict"],
                    "release": f"{RELEASE_BADGE.get(r['release_status'], '')} {r['release_status']}",
                    "signed": f"{r['signed_required']} / {r['required_total']}",
                    "pending": ", ".join(r["pending"]),
                    "released by": r["released_by"] or "",
                }
                for r in q
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

elif page == "Counterparty":
    header("Counterparty risk")
    cps = client.counterparties(run_id)
    if not cps["summary"]:
        st.info("No counterparty exposure stored for this run. Run `uv run novera run counterparty`.")
        st.stop()
    notes = cps["notes"]
    st.caption(
        f"{notes.get('paths')} Monte Carlo paths · grid {notes.get('grid')} · margin period "
        f"{notes.get('margin_period_days')} days · own spread {notes.get('own_spread_bp')}bp · LGD "
        f"{notes.get('lgd')} (CR-001 to CR-004)"
    )
    summ = df(cps["summary"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total EPE", money(summ["epe"].sum()))
    c2.metric("Total CVA", money(summ["cva"].sum(), digits=2), f"DVA {money(summ['dva'].sum(), digits=2)}")
    top = summ.iloc[0]
    c3.metric("Largest PFE 95", money(top["peak_pfe95"]), top["counterparty_id"])
    c4.metric("Wrong-way flags", int(summ["wrong_way"].fillna(False).sum()))
    show = summ[
        [
            "counterparty_id",
            "name",
            "counterparty_type",
            "rating",
            "collateralised",
            "current_exposure",
            "epe",
            "eepe",
            "peak_pfe95",
            "peak_pfe95_step",
            "peak_pfe95_gross",
            "cva",
            "dva",
            "bcva",
            "wwr_correlation",
            "wrong_way",
            "on_watchlist",
            "trades",
        ]
    ].copy()
    for c_ in ("current_exposure", "epe", "eepe", "peak_pfe95", "peak_pfe95_gross", "cva", "dva", "bcva"):
        show[c_] = show[c_] / M
    st.dataframe(show.round(2), use_container_width=True, hide_index=True)
    st.caption(
        "Amounts in millions. PFE after collateral; gross is before. Counterparty limits use peak PFE 95."
    )

    pick = st.selectbox("Counterparty", list(summ["counterparty_id"]))
    d = client.counterparty(pick, run_id=run_id)
    cp = d["counterparty"] or {}
    st.subheader(
        f"{cp.get('name', pick)} · {cp.get('counterparty_type')} · {cp.get('rating')} · {cp.get('country')}"
        + (" · WATCHLIST" if cp.get("on_watchlist") else "")
    )
    prof = df(d["profile"])
    if not prof.empty:
        cols = ["ee_gross", "pfe95_gross", "ee", "pfe95", "mean_collateral"]
        if "initial_margin" in prof and prof["initial_margin"].fillna(0).abs().sum() > 0:
            cols.append("initial_margin")
        chart = prof.set_index("step")[cols] / M
        st.line_chart(
            chart.rename(
                columns={
                    "ee_gross": "EE gross",
                    "pfe95_gross": "PFE95 gross",
                    "ee": "EE",
                    "pfe95": "PFE95",
                    "mean_collateral": "collateral held",
                    "initial_margin": "initial margin",
                }
            )
        )
    left, right = st.columns(2)
    with left:
        st.markdown("**Netting sets and CSA terms**")
        for ns in d["netting_sets"]:
            csa = ns.get("csa")
            terms = (
                f"threshold they post {csa['threshold_they_post'] / M:.1f}m, we post "
                f"{csa['threshold_we_post'] / M:.1f}m, MTA {csa['minimum_transfer_amount'] / M:.2f}m, IA "
                f"{csa['independent_amount'] / M:.1f}m, MPoR {csa['margin_period_of_risk_days']}d"
                if csa
                else "no CSA (uncollateralised)"
            )
            st.markdown(
                f"- `{ns['netting_set_id']}` · {ns['legal_entity_id']} · {ns['agreement_type']} · {terms}"
            )
        wwr = df(d["wwr"])
        if not wwr.empty:
            st.markdown("**Wrong-way risk**")
            st.dataframe(
                wwr[["netting_set_id", "proxy", "correlation", "at_step", "wrong_way"]].round(2),
                hide_index=True,
                use_container_width=True,
            )
    with right:
        st.markdown("**Current exposure under stress (pre-collateral)**")
        stx = df(d["stressed"])
        if not stx.empty:
            stx = stx[["scenario_id", "current_exposure", "stressed_exposure", "increase"]].copy()
            for c_ in ("current_exposure", "stressed_exposure", "increase"):
                stx[c_] = stx[c_] / M
            st.dataframe(
                stx.sort_values("increase", ascending=False).round(1),
                hide_index=True,
                use_container_width=True,
            )
    st.subheader("What if the CSA terms change?")
    ns_ids = [ns["netting_set_id"] for ns in d["netting_sets"]]
    if ns_ids:
        with st.form("csa_whatif"):
            w1, w2, w3, w4, w5 = st.columns(5)
            ns_pick = w1.selectbox("Netting set", ns_ids)
            thr = w2.number_input("Threshold they post (m)", value=0.0, min_value=0.0)
            mta = w3.number_input("MTA (m)", value=0.5, min_value=0.0)
            ia = w4.number_input("Independent amount (m)", value=0.0, min_value=0.0)
            uncoll = w5.checkbox("Uncollateralised")
            if st.form_submit_button("Re-collateralise"):
                res = client.csa_what_if(
                    ns_pick,
                    run_id=run_id,
                    threshold_they_post=thr * M,
                    minimum_transfer_amount=mta * M,
                    independent_amount=ia * M,
                    uncollateralised=uncoll,
                )
                st.session_state["csa_res"] = res
        res = st.session_state.get("csa_res")
        if res and res["netting_set_id"] in ns_ids:
            a, b = st.columns(2)
            a.metric("Peak PFE 95 before", money(res["peak_pfe95_before"]))
            b.metric(
                "Peak PFE 95 after",
                money(res["peak_pfe95_after"]),
                money(res["peak_pfe95_after"] - res["peak_pfe95_before"]),
            )
            before = df(res["before"]).set_index("step")["pfe95"] / M
            after = df(res["after"]).set_index("step")["pfe95"] / M
            st.line_chart(pd.DataFrame({"PFE95 before": before, "PFE95 after": after}))
            st.caption(
                f"Same simulated paths, re-collateralised from the stored values (run {res['run_id']}). "
                "The engine is not rerun; nothing else changes."
            )
    st.subheader("Trades facing this counterparty")
    tr = df(d["trades"])
    if not tr.empty:
        tr["pv"] = tr["pv"] / M
        st.dataframe(tr.round(2), hide_index=True, use_container_width=True)

elif page == "Fund":
    header("Fund risk and investor view")
    fd = client.fund(run_id)
    if not fd.get("available"):
        st.info("No fund run stored for this run.")
        st.stop()
    sm, fund = fd["summary"], fd["fund"] or {}
    nav = sm["nav"]
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("NAV", money(nav, digits=0))
    c2.metric("Gross leverage", f"{sm['gross_leverage']:.2f}x", f"net {sm['net_leverage']:.2f}x")
    c3.metric("VaR 99% 1d", f"{(fd['var'] or 0) / nav:.2%} of NAV", money(fd["var"], digits=1))
    c4.metric("Worst stress", f"{(fd['worst_stress'] or 0) / nav:.1%} of NAV", fd["worst_stress_name"])
    c5.metric(
        "PB margin", f"{sm['margin_to_nav']:.1%} of NAV", f"largest broker {sm['largest_pb_share']:.0%}"
    )
    c6.metric(
        "Crowding", f"{sm['crowding_score']:.2f}", f"{sm['crowded_share_of_gross']:.0%} of gross crowded"
    )
    for f in fd["flags"]:
        st.warning(f)
    st.subheader("Exposure by strategy (% of NAV)")
    es = df(fd["exposure_strategy"])
    if not es.empty:
        show = es[["desk_id", "long_pct_nav", "short_pct_nav", "gross_pct_nav", "net_pct_nav"]].copy()
        for c_ in show.columns[1:]:
            show[c_] = (show[c_] * 100).round(1)
        st.dataframe(show.rename(columns={"desk_id": "strategy"}), use_container_width=True, hide_index=True)
        st.bar_chart(
            show.set_index("desk_id")[["long_pct_nav", "short_pct_nav"]].assign(
                short_pct_nav=lambda d: -d["short_pct_nav"]
            )
        )
    st.subheader("Strategy attribution")
    at = df(fd["attribution"])
    if not at.empty:
        show = at.copy()
        for c_ in ("pv", "component_var", "pnl_today", "max_drawdown"):
            show[c_] = show[c_] / M
        for c_ in (
            "gross_pct_nav",
            "net_pct_nav",
            "share_of_var",
            "hypothetical_ann_return_pct_nav",
            "hypothetical_ann_vol_pct_nav",
        ):
            show[c_] = (show[c_] * 100).round(1)
        st.dataframe(show.round(2), use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        st.subheader("Prime-broker margin")
        mp = df(fd["margin_pb"])
        if not mp.empty:
            mp["margin"] = mp["margin"] / M
            mp["gross_exposure"] = mp["gross_exposure"] / M
            mp["share"] = (mp["share"] * 100).round(0)
            st.dataframe(mp.round(1), hide_index=True, use_container_width=True)
        st.subheader("Redemption stress")
        rd = df(fd["redemptions"])
        if not rd.empty:
            for c_ in ("redemptions_due", "cumulative_due", "liquidatable_by_then", "gated", "shortfall"):
                rd[c_] = rd[c_] / M
            st.dataframe(
                rd[
                    [
                        "scenario",
                        "dealing_date",
                        "business_days",
                        "cumulative_due",
                        "liquidatable_by_then",
                        "coverage",
                        "gated",
                        "shortfall",
                    ]
                ].round(1),
                hide_index=True,
                use_container_width=True,
            )
    with right:
        st.subheader("Factor betas (P&L per one-sigma factor move, % of NAV)")
        fb = df(fd["factors"])
        if not fb.empty:
            piv = fb.pivot_table(index="strategy", columns="label", values="beta_pct_nav", aggfunc="sum")
            st.dataframe((piv * 100).round(2), use_container_width=True)
            st.caption(
                "t-statistics above 2 in absolute value are significant; R² per strategy in the detail."
            )
        st.subheader("Crowding")
        cr = df(fd["crowding"])
        if not cr.empty:
            cr = cr.head(15).copy()
            cr["pct_nav"] = (cr["pct_nav"] * 100).round(1)
            st.dataframe(
                cr[
                    ["underlying", "pct_nav", "score", "crowded", "days_to_liquidate", "crowded_exit_days"]
                ].round(1),
                hide_index=True,
                use_container_width=True,
            )
    if fund:
        st.subheader("Investor register")
        inv = df(fund["investors"])
        inv["share_of_nav"] = (inv["share_of_nav"] * 100).round(1)
        st.dataframe(
            inv[
                [
                    "name",
                    "investor_type",
                    "share_of_nav",
                    "dealing",
                    "notice_days",
                    "gate_pct",
                    "lockup_until",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )
    st.caption(
        "Exposures are delta-equivalent notionals (HF-001); margin schedules, crowding scores and factor "
        "set are synthetic assumptions documented in HF-002 to HF-006."
    )

elif page == "Capital":
    header("Regulatory capital")
    cap = client.capital(run_id)
    if not cap.get("available"):
        st.info("No regulatory run stored for this run. Run `uv run novera run regulatory`.")
        st.stop()
    sm = cap["summary"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("FRTB SA", money(sm["frtb_sa"]), f"SBM {money(sm['frtb_sa_sbm'])}")
    c2.metric("FRTB IMA", money(sm["frtb_ima"]), f"IMES {money(sm['imes'])} x {sm['ima_multiplier']:.2f}")
    c3.metric("SA-CCR capital", money(sm["saccr_capital"]), f"EAD {money(sm['saccr_ead'])}")
    c4.metric("BA-CVA capital", money(sm["ba_cva_capital"]))
    c5.metric("SIMM-lite IM", money(sm["simm_im"]), f"NMRF {int(sm['nmrf'])}")
    st.caption(
        "FRTB SA (REG-001) and IMA (REG-002) are alternatives; SA-CCR (REG-003), BA-CVA (REG-005) and the "
        "initial margin (REG-004) add to them. Parameters are published-style, not a licensed calibration."
    )
    st.subheader("Capital by component")
    comp = df(cap["components"])
    if not comp.empty:
        comp["capital"] = comp["capital"] / M
        st.bar_chart(comp.set_index("component")["capital"])
    st.subheader("By desk (standardised stack: FRTB SA + SA-CCR + BA-CVA; IMA shown for comparison)")
    bd = df(cap["by_desk"])
    if not bd.empty:
        for c_ in ("frtb_sa", "frtb_ima", "saccr", "ba_cva", "total_sa"):
            bd[c_] = bd[c_] / M
        st.dataframe(bd.round(1), use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        st.subheader("FRTB SA by risk class")
        cl = df(cap["frtb_sa_classes"])
        if not cl.empty:
            for c_ in ("delta", "vega", "curvature"):
                cl[c_] = cl[c_] / M
            st.dataframe(cl.round(1), hide_index=True, use_container_width=True)
        st.subheader("P&L attribution test by desk")
        pla = df(cap["pla"])
        if not pla.empty:
            st.dataframe(pla.round(3), hide_index=True, use_container_width=True)
    with right:
        st.subheader("SA-CCR by counterparty")
        sc = df(cap["saccr_counterparty"])
        if not sc.empty:
            for c_ in ("ead", "rc", "pfe", "rwa", "capital"):
                sc[c_] = sc[c_] / M
            st.dataframe(sc.round(1), hide_index=True, use_container_width=True)
        st.subheader("Initial margin by netting set")
        sim = df(cap["simm"])
        if not sim.empty:
            sim["im"] = sim["im"] / M
            st.dataframe(
                sim[["netting_set_id", "counterparty_id", "im"]].round(1).head(15),
                hide_index=True,
                use_container_width=True,
            )
    st.subheader("Funding cash ladder (contractual, reporting currency)")
    lad = df(cap["cash_ladder"])
    if not lad.empty:
        ccy = st.selectbox("Currency", sorted(lad["currency"].unique()), index=0)
        sub = lad[lad["currency"] == ccy].copy()
        for c_ in ("inflow", "outflow", "net", "cumulative_net"):
            sub[c_] = sub[c_] / M
        st.dataframe(sub.round(1), hide_index=True, use_container_width=True)
        st.bar_chart(sub.set_index("bucket")["net"])

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
    from novera.api.errors import ApiError

    def act(fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
            st.cache_data.clear()
            st.rerun()
        except ApiError as e:
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
                if st.button("Investigate with the agent", key=f"inv_{b['breach_id']}"):
                    with st.spinner("Gathering evidence from the stored runs and drafting the note..."):
                        note_d = client.investigate_breach(b["breach_id"], True)
                    st.markdown(note_d["text"])
                    st.caption(
                        f"note {note_d['note_id']} attached to the breach · "
                        f"{note_d['provider']}/{note_d['model']}"
                    )
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
    st.subheader("Fund look-through")
    lt = client.lookthrough(run_id)
    if not lt["fund_trades"]:
        st.caption("No ETF or mutual-fund positions in this run.")
    else:
        for fl in lt["flags"]:
            st.warning(fl)
        bf = df(lt["by_fund"])
        if not bf.empty:
            bf["exposure"] = bf["exposure"] / M
            st.dataframe(
                bf.round(2).rename(columns={"exposure": "exposure (m)"}),
                use_container_width=True,
                hide_index=True,
            )
        cons = df(lt["constituents"])
        if not cons.empty:
            for c in ("direct", "via_funds", "total"):
                cons[c] = cons[c] / M
            cons["via_funds_share"] = (cons["via_funds_share"] * 100).round(0)
            st.dataframe(
                cons.head(25)
                .round(2)
                .rename(
                    columns={
                        "direct": "direct (m)",
                        "via_funds": "via funds (m)",
                        "total": "total (m)",
                        "via_funds_share": "via funds %",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
        with st.expander("Holdings by fund trade"):
            h = df(lt["holdings"])
            if not h.empty:
                h["exposure"] = h["exposure"] / M
                h["share_of_fund"] = (h["share_of_fund"] * 100).round(1)
                st.dataframe(
                    h.round(2).rename(
                        columns={"exposure": "exposure (m)", "share_of_fund": "share of fund %"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
    st.caption(
        "Volume and bid-ask assumptions are synthetic (MR-013). Concentration uses component VaR "
        "where available (MR-012). Look-through per MR-014: pricing and sensitivities already see "
        "through funds; this table shows how much of each constituent comes via funds."
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
            html = client.risk_pack_content(run_id, "html").decode("utf-8")
            st.components.v1.html(html, height=900, scrolling=True)

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
    st.subheader("Market-data proxies applied before pricing")
    px = client.market_data_proxies(run_id)
    if not px["actions"]:
        st.caption("No proxies were needed: every factor was present and current.")
    else:
        st.write(
            f"{px['applied']} factor values proxied, {px['kept_stale']} stale values kept as observed. "
            f"The raw snapshot {px['raw_market_snapshot_id']} is unchanged; the run priced off the "
            "proxied copy (MD-002)."
        )
        pa = df(px["actions"])
        st.dataframe(
            pa[["factor_id", "kind", "source", "original", "value", "reason"]],
            use_container_width=True,
            hide_index=True,
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

elif page == "Agents":
    header("Agents")
    st.caption(
        "Each agent gathers its evidence deterministically from the stored runs and the engine, then drafts "
        "text from that evidence only (AI-002, AI-003). Notes are stored with their evidence and audited."
    )
    tab_sc, tab_val, tab_csa, tab_notes = st.tabs(
        ["Scenario suggestions", "Model validation", "CSA ingestion", "Notes"]
    )
    with tab_sc:
        n_sc = st.slider("Scenarios to propose", 2, 6, 4)
        if st.button("Suggest scenarios for this run"):
            with st.spinner("Sizing scenarios from the history and running them through the engine..."):
                d = client.suggest_scenarios(run_id, n_sc)
            st.markdown(d["text"])
            props = df(d["evidence"].get("proposals", []))
            if not props.empty and "total_pnl_m" in props:
                st.dataframe(
                    props[["name", "why", "total_pnl_m"]].rename(columns={"total_pnl_m": "P&L (m)"}),
                    use_container_width=True,
                    hide_index=True,
                )
            st.caption(f"note {d['note_id']} · {d['provider']}/{d['model']} · {d['seconds']}s")
    with tab_val:
        recs = st.text_input("Records (comma-separated ids or families, empty = all)", value="PR,MR")
        run_tests = st.checkbox("Execute the named validation tests (slow)", value=False)
        if st.button("Draft the validation report"):
            with st.spinner("Collecting evidence and drafting..."):
                d = client.draft_validation(
                    run_id, [r.strip() for r in recs.split(",") if r.strip()] or None, run_tests
                )
            st.markdown(d["text"])
            st.caption(f"written to {d.get('path')} · note {d['note_id']}")
    with tab_csa:
        st.write(
            "Parse a CSA term sheet into a proposed netting set and CSA, review, then approve or reject."
        )
        docs = sorted(
            str(p) for p in (settings.data_dir / "documents").glob("*") if p.suffix in (".txt", ".md", ".pdf")
        )
        path = st.selectbox("Document", docs) if docs else st.text_input("Document path")
        if not docs:
            st.caption("No documents yet: `uv run novera agent ingest` writes a demo term sheet.")
        if path and st.button("Propose from the document"):
            with st.spinner("Extracting terms..."):
                d = client.propose_csa(path)
            st.session_state["csa_note"] = d
        d = st.session_state.get("csa_note")
        if d:
            st.markdown(d["text"])
            st.json(d["evidence"]["proposal"])
            approver = st.text_input("Approver", value="Head of Counterparty Risk")
            c1, c2 = st.columns(2)
            if c1.button("Approve and save"):
                try:
                    out = client.approve_csa(d["note_id"], approver)
                    st.success(f"saved netting set {out['netting_set_id']} and CSA {out['csa_id']}")
                    st.session_state.pop("csa_note", None)
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
            if c2.button("Reject"):
                client.reject_csa(d["note_id"], approver, "rejected in review")
                st.session_state.pop("csa_note", None)
                st.info("rejected")
    with tab_notes:
        notes = df(client.agent_notes(limit=50))
        if notes.empty:
            st.caption("No agent notes yet.")
        else:
            st.dataframe(
                notes[["at", "kind", "subject", "status", "provider", "note_id"]],
                use_container_width=True,
                hide_index=True,
            )
            pick = st.selectbox("Open note", notes["note_id"])
            st.markdown(notes.set_index("note_id").loc[pick, "text"])

elif page == "Portfolio Lab":
    header("Portfolio Lab")
    st.caption(
        "Plant chosen problems at a chosen size in a sandbox organisation, run the governed EOD on it and "
        "see what the platform detects (LAB-001). Nothing here touches the production databases."
    )
    cat = client.problem_catalogue()
    with st.form("lab"):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Lab name", value="lab_demo")
        template = c2.selectbox("Template", ["bank", "hedge_fund"])
        scale = c3.slider("Problem size multiplier", 0.25, 5.0, 1.0, 0.25)
        options = {x["name"]: x["title"] for x in cat[template]}
        problems = st.multiselect(
            "Problems to plant", list(options), default=list(options)[:2], format_func=options.get
        )
        mkt = {x["name"]: x["title"] for x in cat["market"]}
        market_problems = st.multiselect("Market-data problems", list(mkt), format_func=mkt.get)
        c4, c5, c6 = st.columns(3)
        n_trades = c4.number_input("Background trades", 100, 3000, 600, 100)
        years = c5.number_input("Years of history", 1.0, 5.0, 2.0, 0.5)
        cpty = c6.checkbox("Run the counterparty engine (slower; needed for wrong-way problems)")
        go = st.form_submit_button("Run the lab")
    if go:
        with st.spinner("Simulating, running EOD and reading back the detections (about a minute)..."):
            res = client.run_lab(
                {
                    "name": name,
                    "template": template,
                    "problems": problems,
                    "market_problems": market_problems,
                    "scale": scale,
                    "n_trades": int(n_trades),
                    "years": float(years),
                    "counterparty": bool(cpty),
                }
            )
        st.session_state["lab_result"] = res
    res = st.session_state.get("lab_result")
    if res:
        sm = res["summary"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Detected", f"{res['detected']} / {res['planted']}")
        c2.metric("VaR", money(sm.get("var"), digits=2))
        c3.metric("Breaches / warnings", f"{sm.get('breaches')} / {sm.get('warnings')}")
        c4.metric("Verdict", sm.get("verdict"))
        st.caption(f"run {res['run_id']} in {res['db_path']} · {res['seconds']}s")
        for d in res["detections"]:
            with st.expander(
                ("✅ " if d["detected"] else "❌ ") + f"{d['title']} — {d['description']}", expanded=True
            ):
                st.write(f"Expected: {d['expected']}")
                for e in d["evidence"]:
                    st.markdown(f"- {e}")
                for c in d.get("context", []):
                    st.markdown(f"- _{c}_")
                if d.get("needs"):
                    st.warning(f"needs {d['needs']}")
    labs = client.labs()
    if labs:
        st.subheader("Previous labs")
        rows = [
            {
                "name": x["name"],
                "run_id": x.get("run_id"),
                "detected": sum(1 for d in (x.get("detections") or []) if d["detected"]),
                "planted": len(x.get("detections") or []),
                "template": (x.get("spec") or {}).get("template"),
                "scale": (x.get("spec") or {}).get("scale"),
            }
            for x in labs
        ]
        st.dataframe(df(rows), use_container_width=True, hide_index=True)

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

elif page == "Market data":
    header("Market data sources")
    st.caption(
        "Where every risk factor's history comes from today, and where real data could come "
        "from. Status is decided from the store and the adapter maps, never by hand (MD-001)."
    )

    @st.cache_data(ttl=60, show_spinner=False)
    def load_sources(firm: str) -> dict:
        return client.market_data_sources()

    src = load_sources(firm_name)
    tot = src["summary"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Risk factors", f"{tot['factors']:,}")
    c2.metric("Real, fetched", f"{tot['real']:,}")
    c3.metric("Real source wired", f"{tot['available']:,}", "not fetched yet")
    c4.metric("Synthetic", f"{tot['synthetic']:,}", f"{tot['synthetic'] / max(tot['factors'], 1):.0%}")
    c5.metric("Families fully synthetic", f"{tot['families_synthetic']} / {tot['families']}")
    if not tot["real"]:
        st.info(
            "Nothing has been fetched into this store. `uv run novera fetch` pulls the wired "
            "sources (FRED needs a free key in `FRED_API_KEY`); fetched dates replace the "
            "synthetic values and every other factor stays simulated."
        )

    status_names = {
        "REAL": "🟢 Real, fetched",
        "PARTIAL": "🟡 Partly real or fetchable",
        "AVAILABLE": "🔵 Real source wired, not fetched",
        "SYNTHETIC": "⚪ Synthetic",
    }
    ac_names = {
        "RATES": "Rates",
        "FX": "FX",
        "EQUITY": "Equity",
        "CREDIT": "Credit",
        "COMMODITY": "Commodities",
        "DIGITAL_ASSET": "Digital assets",
    }
    fams = df(src["families"])
    f1, f2, f3 = st.columns([2, 2, 3])
    pick_status = f1.multiselect(
        "Status",
        list(status_names),
        default=list(status_names),
        format_func=status_names.get,
        key="md_status",
    )
    pick_ac = f2.multiselect(
        "Asset class",
        list(ac_names),
        default=list(ac_names),
        format_func=ac_names.get,
        key="md_ac",
    )
    q = f3.text_input(
        "Filter", placeholder="family, underlying or source, e.g. VOL:SPX or Bloomberg", key="md_q"
    )
    q = q.strip().lower()
    mask = fams["status"].isin(pick_status) & fams["asset_class"].isin(pick_ac)
    if q:
        text_cols = ["family", "group", "underlying", "free_source", "paid_source", "notes"]
        mask &= fams[text_cols].astype(str).apply(lambda r: q in " ".join(r).lower(), axis=1)
    shown = fams[mask].copy()
    order = {s_: i for i, s_ in enumerate(status_names)}
    ac_order = {a: i for i, a in enumerate(ac_names)}
    shown = shown.sort_values(
        ["status", "asset_class", "family"],
        key=lambda c: c.map(order if c.name == "status" else ac_order if c.name == "asset_class" else str),
    )
    if shown.empty:
        st.info("Nothing matches the filter.")
    else:
        view = st.radio("Show", ["By family", "By source group"], horizontal=True, key="md_view")
        if view == "By family":
            table = pd.DataFrame(
                {
                    "family": shown["family"],
                    "group": shown["group"],
                    "status": shown["status"].map(status_names),
                    "nodes": shown["factors"],
                    "real": shown["real"],
                    "wired": shown["available"],
                    "synthetic": shown["synthetic"],
                    "current source": shown["sources"].map(", ".join),
                    "last real date": shown["last_date"].fillna("—"),
                    "free source": shown["free_source"],
                    "paid source": shown["paid_source"],
                    "notes": shown["notes"],
                }
            )
            st.dataframe(
                table, hide_index=True, use_container_width=True, height=min(60 + 35 * len(table), 700)
            )
            st.caption(
                f"{len(shown)} families · {int(shown['factors'].sum()):,} factors · "
                f"'wired' means an adapter maps the node but `novera fetch` has not been run for it"
            )
            with st.expander("Factor detail for the families shown"):
                rows = df(src["rows"])
                rows = rows[rows["family"].isin(shown["family"])]
                detail = pd.DataFrame(
                    {
                        "factor": rows["factor_id"],
                        "status": rows["status"].map(status_names),
                        "current source": rows["source"],
                        "adapter": rows["adapter"].fillna("—"),
                        "first real date": rows["first_date"].fillna("—"),
                        "last real date": rows["last_date"].fillna("—"),
                        "real rows": rows["row_count"].fillna(0).astype(int),
                    }
                )
                st.dataframe(detail, hide_index=True, use_container_width=True, height=500)
        else:
            for group, g in shown.groupby("group", sort=True):
                counts = " · ".join(f"{status_names[s_]} {n}" for s_, n in g["status"].value_counts().items())
                n_fam = f"{len(g)} famil{'y' if len(g) == 1 else 'ies'}"
                label = f"{group} · {n_fam} · {int(g['factors'].sum()):,} factors · {counts}"
                with st.expander(label, expanded=bool(q)):
                    first = g.iloc[0]
                    st.markdown(
                        f"- **Families:** {', '.join(sorted(g['family']))}\n"
                        f"- **Current source:** {', '.join(sorted(set(sum(g['sources'].tolist(), []))))}\n"
                        f"- **Free source:** {first['free_source']}\n"
                        f"- **Paid source:** {first['paid_source']}"
                        + (f"\n- **Note:** {first['notes']}" if first["notes"] else "")
                    )

elif page == "Reference data":
    header("Reference data")
    st.caption(
        "What the runs are priced and measured against: organisation, counterparties, products, "
        "risk measures and risk factors, as stored and as coded. No run figures on this page."
    )
    what = st.radio(
        "Show",
        ["Organisation", "Counterparties", "Products", "Model inventory", "Risk measures", "Risk factors"],
        horizontal=True,
        key="ref_what",
    )
    q = st.text_input("Filter", placeholder="id or name, e.g. USD_RATES or Bank A", key="ref_filter")
    q = q.strip().lower()

    def _hit(*parts) -> bool:
        return not q or any(q in str(x).lower() for x in parts if x)

    def _amt(x: float) -> str:
        if x >= 1e6:
            return f"{x / 1e6:g}m"
        if x >= 1e3:
            return f"{x / 1e3:g}k"
        return f"{x:g}"

    def _words(x: str) -> str:
        return x.replace("_", " ").lower()

    shown = 0
    if what == "Organisation":
        org = client.organisation()
        firm = org["firm"]
        entities = {e["legal_entity_id"]: e for e in org["legal_entities"]}
        books_by_desk: dict[str, list[dict]] = {}
        traders_by_desk: dict[str, list[str]] = {}
        for bk in org["books"]:
            books_by_desk.setdefault(bk["desk_id"], []).append(bk)
        for tr in org["traders"]:
            traders_by_desk.setdefault(tr["desk_id"], []).append(tr["name"])
        st.markdown(
            f"**{firm['name']}** `{firm['firm_id']}` · {_words(firm['firm_type'])} · "
            f"{len(entities)} legal entities · {len(org['businesses'])} businesses · "
            f"{len(org['desks'])} desks · {len(org['books'])} books · {len(org['traders'])} traders"
        )
        group = st.radio("Group by", ["Business", "Legal entity"], horizontal=True, key="ref_group")

        def render_desk(d: dict, books: list[dict], expanded: bool) -> None:
            head = f" · head {d['head']}" if d.get("head") else ""
            label = (
                f"{d['name']} · {d['desk_id']} · {_words(d['asset_class'])} · {d['region']}{head} · "
                f"{len(books)} book{'s' if len(books) != 1 else ''}"
            )
            with st.expander(label, expanded=expanded):
                lines = []
                for bk in books:
                    strat = f" · {bk['strategy']}" if bk.get("strategy") else ""
                    ent = entities.get(bk["legal_entity_id"], {}).get("name", "")
                    lines.append(
                        f"- `{bk['book_id']}` {bk['name']}{strat} · entity `{bk['legal_entity_id']}` {ent}"
                    )
                if traders_by_desk.get(d["desk_id"]):
                    lines.append(f"- traders: {', '.join(traders_by_desk[d['desk_id']])}")
                st.markdown("\n".join(lines) if lines else "_no books_")

        def desks_to_show(
            parent_hit: bool, desks: list[dict], books_of: dict[str, list[dict]]
        ) -> list[tuple[dict, list[dict]]]:
            """Desks to show under one parent: all of them if the parent matches the filter,
            otherwise matching desks with all their books, and desks with matching books."""
            out: list[tuple[dict, list[dict]]] = []
            for d in desks:
                books = books_of.get(d["desk_id"], [])
                if parent_hit or _hit(d["desk_id"], d["name"]):
                    out.append((d, books))
                else:
                    match = [bk for bk in books if _hit(bk["book_id"], bk["name"], bk.get("strategy"))]
                    if match:
                        out.append((d, match))
            return out

        if group == "Business":
            for b in org["businesses"]:
                desks = [d for d in org["desks"] if d["business_id"] == b["business_id"]]
                items = desks_to_show(_hit(b["business_id"], b["name"]), desks, books_by_desk)
                if not items:
                    continue
                shown += 1
                n_books = sum(len(books_by_desk.get(d["desk_id"], [])) for d in desks)
                label = f"{b['name']} · {b['business_id']} · {len(desks)} desks · {n_books} books"
                with st.expander(label, expanded=bool(q)):
                    for d, books in items:
                        render_desk(d, books, expanded=bool(q))
        else:
            for e in org["legal_entities"]:
                eid = e["legal_entity_id"]
                entity_books: dict[str, list[dict]] = {}
                for bk in org["books"]:
                    if bk["legal_entity_id"] == eid:
                        entity_books.setdefault(bk["desk_id"], []).append(bk)
                desks = [d for d in org["desks"] if d["desk_id"] in entity_books]
                n_books = sum(len(v) for v in entity_books.values())
                items = desks_to_show(_hit(eid, e["name"], e["jurisdiction"]), desks, entity_books)
                if not items:
                    continue
                shown += 1
                with st.expander(
                    f"{e['name']} · {eid} · {e['jurisdiction']} · {e['functional_currency']} · "
                    f"{len(desks)} desks · {n_books} books",
                    expanded=bool(q),
                ):
                    for d, books in items:
                        render_desk(d, books, expanded=bool(q))
    elif what == "Counterparties":
        ref = client.counterparty_reference()
        csas = {c["csa_id"]: c for c in ref["csas"]}
        ns_by_cp: dict[str, list[dict]] = {}
        for n in ref["netting_sets"]:
            ns_by_cp.setdefault(n["counterparty_id"], []).append(n)
        cps = ref["counterparties"]
        children: dict[str, list[dict]] = {}
        for c in cps:
            if c.get("parent_id"):
                children.setdefault(c["parent_id"], []).append(c)
        uncoll = sum(1 for n in ref["netting_sets"] if not n.get("csa_id"))
        watch = sum(1 for c in cps if c.get("on_watchlist"))
        st.markdown(
            f"{len(cps)} counterparties · {len(ref['netting_sets'])} netting sets "
            f"({uncoll} uncollateralised) · {len(csas)} CSAs · {watch} on watchlist"
        )

        def cp_lines(c: dict, indent: str = "") -> list[str]:
            pd_txt = f" · PD 1y {c['internal_pd'] * 1e4:.0f}bp" if c.get("internal_pd") is not None else ""
            flag = " · **on watchlist**" if c.get("on_watchlist") else ""
            sector = c.get("sector") or "sector n/a"
            lines = [f"{indent}- {sector} · {c['country']} · rating {c['rating']}{pd_txt}{flag}"]
            for n in ns_by_cp.get(c["counterparty_id"], []):
                lines.append(
                    f"{indent}- **`{n['netting_set_id']}`** · our entity `{n['legal_entity_id']}` · "
                    f"{n['agreement_type']}"
                )
                csa = csas.get(n.get("csa_id") or "")
                if csa:
                    lines.append(
                        f"{indent}    - CSA `{csa['csa_id']}` · {csa['collateral_currency']} · "
                        f"threshold we post {_amt(csa['threshold_we_post'])} / they post "
                        f"{_amt(csa['threshold_they_post'])} · MTA {_amt(csa['minimum_transfer_amount'])} · "
                        f"IA {_amt(csa['independent_amount'])} · rounding {_amt(csa['rounding'])} · "
                        f"haircut {csa['haircut']:.0%} · MPoR {csa['margin_period_of_risk_days']}d · "
                        f"{csa['call_frequency'].lower()} calls"
                    )
                else:
                    lines.append(f"{indent}    - uncollateralised")
            return lines

        for c in cps:
            if c.get("parent_id"):
                continue  # shown under its parent
            subs = children.get(c["counterparty_id"], [])
            family = [c, *subs]
            sets = [n for x in family for n in ns_by_cp.get(x["counterparty_id"], [])]
            match = any(_hit(x["counterparty_id"], x["name"]) for x in family) or any(
                _hit(n["netting_set_id"], n.get("csa_id")) for n in sets
            )
            if not match:
                continue
            shown += 1
            n_ns = sum(len(ns_by_cp.get(x["counterparty_id"], [])) for x in family)
            title = (
                f"{c['name']} · {c['counterparty_id']} · {_words(c['counterparty_type'])} · "
                f"{c['rating']} · {c['country']}"
                + (f" · {len(subs)} subsidiaries" if subs else "")
                + f" · {n_ns} netting sets"
                + (" · ⚠ watchlist" if c.get("on_watchlist") else "")
            )
            with st.expander(title, expanded=bool(q)):
                lines = cp_lines(c)
                for sub in subs:
                    lines.append(
                        f"- **{sub['name']}** `{sub['counterparty_id']}` · "
                        f"{_words(sub['counterparty_type'])} · subsidiary"
                    )
                    lines += cp_lines(sub, indent="    ")
                st.markdown("\n".join(lines))
    elif what == "Products":
        ref = client.product_reference()
        n_products = sum(len(a["products"]) for a in ref["asset_classes"])
        st.markdown(
            f"{len(ref['asset_classes'])} asset classes · {n_products} products · one pricer per product, "
            "each governed by a methodology record in `docs/methodology/`"
        )
        for ac in ref["asset_classes"]:
            ac_hit = _hit(ac["asset_class"], ac["name"])
            prods = [
                p
                for p in ac["products"]
                if ac_hit
                or _hit(p["product_type"], p["name"], p["model"], p["model_label"], p["methodology"])
            ]
            if not prods:
                continue
            shown += 1
            label = f"{ac['name']} · {ac['asset_class']} · {len(ac['products'])} products"
            with st.expander(label, expanded=bool(q)):
                for prod in prods:
                    title = (
                        f"{prod['name']} · {prod['product_type']} · {prod['venue'].lower()} · "
                        f"{prod['model_label']}"
                    )
                    with st.expander(title, expanded=bool(q)):
                        st.markdown(
                            f"- venue: {prod['venue'].lower()}\n"
                            f"- model: {prod['model_label']} · `{prod['model']}` v{prod['model_version']}\n"
                            f"- methodology: `{prod['methodology']}` {prod['methodology_title']}\n"
                            f"- instrument: `{prod['instrument_class']}`\n"
                            f"- market standard: {prod['market_standard']}\n"
                            f"- simplifications: {prod['simplifications']}\n"
                            f"- rating: **{prod['appropriateness_label']}** (MV-001)\n"
                            f"- validation: {prod['validation']}"
                        )
                        st.markdown("**Defining fields**")
                        rows = []
                        for f in prod["fields"]:
                            line = f"- `{f['name']}` · {f['type']}"
                            if not f["required"]:
                                empty = f["default"] in ("", None)
                                line += " · optional" if empty else f" · default `{f['default']}`"
                            if f["description"]:
                                line += f" · {f['description']}"
                            rows.append(line)
                        st.markdown("\n".join(rows))
    elif what == "Model inventory":
        inv = client.model_inventory()
        counts = inv["summary"]
        st.markdown(
            f"Record `{inv['record']}` v{inv['version']} · {len(inv['rows'])} products · "
            f"{counts.get('market_standard', 0)} market standard · "
            f"{counts.get('acceptable_simplification', 0)} acceptable simplification · "
            f"{counts.get('known_weakness', 0)} known weakness. The rating says how far the model "
            "used sits from what a desk would expect; the simplifications column says exactly where. "
            "Read from code, checked against `docs/methodology/MV-001` by the test suite."
        )
        rows = [
            r
            for r in inv["rows"]
            if _hit(r["product_type"], r["name"], r["model"], r["asset_class_name"], r["appropriateness"])
        ]
        shown = len(rows)
        if rows:
            table = pd.DataFrame(
                [
                    {
                        "asset class": r["asset_class_name"],
                        "product": r["name"],
                        "model used": f"{r['model_label']} (v{r['model_version']})",
                        "rating": r["appropriateness_label"],
                        "market standard": r["market_standard"],
                        "simplifications": r["simplifications"],
                        "validation": r["validation"],
                        "record": r["methodology"],
                    }
                    for r in rows
                ]
            )
            shade = {
                "Market standard": "background-color: rgba(46, 160, 67, 0.18)",
                "Acceptable simplification": "background-color: rgba(210, 153, 34, 0.18)",
                "Known weakness": "background-color: rgba(218, 54, 51, 0.18)",
            }
            st.dataframe(
                table.style.map(lambda v: shade.get(v, ""), subset=["rating"]),
                use_container_width=True,
                hide_index=True,
                height=min(80 + 36 * len(table), 800),
            )
            st.caption(
                "Known weaknesses: EUR swaps on a single curve (measured gap in PR-002), "
                "swaptions without a strike smile, barrier options on one flat vol."
            )
    elif what == "Risk measures":
        ref = client.measure_reference()
        n_measures = sum(len(a["measures"]) for a in ref["areas"])
        st.markdown(
            f"{len(ref['areas'])} risk areas · {n_measures} measures · every measure has a methodology "
            "record in `docs/methodology/` (definition, maths, inputs, assumptions, limitations, validation)"
        )
        for area in ref["areas"]:
            area_hit = _hit(area["area"], area["name"])
            ms = [
                m
                for m in area["measures"]
                if area_hit or _hit(m["methodology"], m["name"], m["screen"], m["methodology_title"])
            ]
            if not ms:
                continue
            shown += 1
            with st.expander(f"{area['name']} · {len(area['measures'])} measures", expanded=bool(q)):
                for m in ms:
                    with st.expander(f"{m['name']} · {m['methodology']} · {m['screen']}", expanded=bool(q)):
                        st.markdown(
                            f"{m['definition']}\n\n"
                            f"- unit: {m['unit']}\n"
                            f"- methodology: `{m['methodology']}` {m['methodology_title']} · "
                            f"v{m['version']}\n"
                            f"- screen: {m['screen']}\n"
                            f"- face: {m['face']}"
                        )
    else:
        factors = client.risk_factor_reference()["factors"]
        type_names = {
            "IR_ZERO": "Zero curves",
            "FX_SPOT": "FX spot",
            "EQUITY_SPOT": "Equity spot",
            "EQUITY_INDEX": "Equity indices",
            "COMMODITY_CURVE": "Commodity curves",
            "CREDIT_SPREAD": "Credit spreads",
            "CRYPTO_SPOT": "Crypto spot",
            "IMPLIED_VOL": "Implied vol surfaces",
            "SWAPTION_VOL": "Swaption vol cubes",
            "SWAPTION_SMILE": "Swaption SABR smiles",
        }
        ac_names = {
            "RATES": "Rates",
            "FX": "FX",
            "EQUITY": "Equity",
            "CREDIT": "Credit",
            "COMMODITY": "Commodities",
            "DIGITAL_ASSET": "Digital assets",
        }
        tree: dict[str, dict[str, dict[str, list[dict]]]] = {}
        for f in factors:
            tree.setdefault(f["asset_class"], {}).setdefault(f["factor_type"], {}).setdefault(
                f["underlying"], []
            ).append(f)
        st.markdown(
            f"{len(factors)} risk factors · {len(tree)} asset classes · "
            f"{sum(len(v) for v in tree.values())} factor types · sensitivities, VaR and stress all key off "
            "these ids, so results reconcile"
        )

        def factor_line(underlying: str, fs: list[dict]) -> str:
            first = fs[0]
            kind = first["factor_type"]
            shock = first["shock_type"].lower()
            if len(fs) == 1:
                return (
                    f"- `{first['factor_id']}` **{underlying}** · {first['currency']} · {first['unit']} · "
                    f"{shock} shocks"
                )
            if kind == "IMPLIED_VOL":
                years = {f["tenor"]: f["expiry_years"] for f in fs}
                exp = sorted(years, key=years.get)
                mny = sorted({f["moneyness"] for f in fs})
                return (
                    f"- **{underlying}** · {len(fs)} points · expiries {', '.join(exp)} × moneyness "
                    f"{', '.join(f'{m:.2f}' for m in mny)} · {first['unit']} · {shock} shocks"
                )
            if kind in ("SWAPTION_VOL", "SWAPTION_SMILE"):
                exp = sorted({f["expiry_years"] for f in fs})
                years = {f["tenor"]: f["tenor_years"] for f in fs}
                ten = sorted(years, key=years.get)
                what = (
                    f"{first['unit']} · {shock} shocks"
                    if kind == "SWAPTION_VOL"
                    else "normal SABR rho (absolute shocks) and nu (relative shocks), beta 0"
                )
                return (
                    f"- **{underlying}** · {len(fs)} points · expiries {', '.join(f'{e:g}Y' for e in exp)} × "
                    f"tenors {', '.join(ten)} · {what}"
                )
            nodes = sorted(fs, key=lambda f: f["tenor_years"] or 0)
            return (
                f"- **{underlying}** · {first['currency']} · {len(fs)} nodes: "
                f"{', '.join(n['tenor'] for n in nodes)} · "
                f"{first['unit']} · {shock} shocks"
            )

        for ac in ["RATES", "FX", "EQUITY", "CREDIT", "COMMODITY", "DIGITAL_ASSET"]:
            if ac not in tree:
                continue
            ac_hit = _hit(ac, ac_names.get(ac, ac))
            groups = []
            for kind, by_und in tree[ac].items():
                kind_hit = ac_hit or _hit(kind, type_names.get(kind, kind))
                unds = {
                    u: fs
                    for u, fs in by_und.items()
                    if kind_hit or _hit(u) or any(_hit(f["factor_id"]) for f in fs)
                }
                if unds:
                    groups.append((kind, unds, sum(len(v) for v in by_und.values()), len(by_und)))
            if not groups:
                continue
            shown += 1
            n_ac = sum(len(fs) for by_und in tree[ac].values() for fs in by_und.values())
            label = f"{ac_names.get(ac, ac)} · {ac} · {len(tree[ac])} factor types · {n_ac} factors"
            with st.expander(label, expanded=bool(q)):
                for kind, unds, n_kind, n_und in groups:
                    label = f"{type_names.get(kind, kind)} · {kind} · {n_und} underlyings · {n_kind} factors"
                    with st.expander(label, expanded=bool(q)):
                        st.markdown("\n".join(factor_line(u, fs) for u, fs in sorted(unds.items())))
    if not shown:
        st.info("Nothing matches the filter.")

elif page == "Limit management":
    header("Limit management")
    tab_h, tab_u, tab_b, tab_i = st.tabs(["Hierarchy", "Utilisation", "Breaches", "Increases"])
    with tab_h:
        lh = load("limit_hierarchy", run_id)
        rows = lh["rows"]
        level_names = {
            "firm_id": "Firm",
            "business_id": "Business",
            "desk_id": "Desk",
            "book_id": "Book",
            "counterparty_id": "Counterparty",
        }
        f1, f2, f3, f4 = st.columns([1.3, 1.6, 1.6, 2])
        group = f1.radio("Group by", ["Hierarchy", "Limit type"], horizontal=True, key="lm_group")
        rank = {r["level"]: r["level_rank"] for r in rows}
        levels = sorted(rank, key=rank.get)
        lvl_pick = f2.multiselect(
            "Level", levels, default=levels, format_func=lambda x: level_names.get(x, x), key="lm_levels"
        )
        statuses = ["BREACH", "WARNING", "OK", "NO_DATA", "NO_RUN"]
        st_pick = f3.multiselect("Run status", statuses, default=statuses, key="lm_status")
        q = f4.text_input("Filter", placeholder="limit id, node, type, owner", key="lm_filter")
        q = q.strip().lower()
        rows = [
            r
            for r in rows
            if r["level"] in lvl_pick
            and r["status"] in st_pick
            and (not q or q in " ".join(str(v) for v in r.values() if v is not None).lower())
        ]
        if group == "Limit type":
            rows = sorted(rows, key=lambda r: (r["limit_type"], r["level_rank"], r["path"], r["limit_id"]))

        def _lim_amt(x, unit: str) -> str:
            if x is None:
                return "—"
            return f"{x:.2f}" if unit == "share" else f"{x / M:,.1f}m"

        badge = {"BREACH": "🔴", "WARNING": "🟠", "OK": "🟢", "NO_DATA": "⚪", "NO_RUN": "⚪"}
        table = pd.DataFrame(
            [
                {
                    "status": f"{badge.get(r['status'], '⚪')} {r['status']}",
                    "hierarchy": r["path"],
                    "limit": r["limit_id"],
                    "type": r["limit_type"],
                    "scope": r["filters"],
                    "limit amount": _lim_amt(r["effective_amount"], r["unit"])
                    + (" ↑" if r["increase_id"] else ""),
                    "current": _lim_amt(r["current"], r["unit"]),
                    "utilisation": r["utilisation"],
                    "warning at": r["warning_threshold"],
                    "owner": r["owner"],
                    "approver": r["approver"],
                    "approval": r["approval_status"],
                    "effective from": r["effective_from"],
                }
                for r in rows
            ]
        )
        n_b = sum(1 for r in rows if r["status"] == "BREACH")
        n_w = sum(1 for r in rows if r["status"] == "WARNING")
        st.caption(
            f"{len(rows)} of {len(lh['rows'])} limits · {n_b} in breach · {n_w} in warning · "
            f"amounts in {ccy} millions, shares as fractions · ↑ marks a temporary increase in force"
            + (f" · figures from run {lh['run_id']}" if lh.get("run_id") else " · no run stored")
        )
        if table.empty:
            st.info("No limits match the filters.")
        else:
            event = st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                height=min(38 * (len(table) + 1) + 4, 900),
                on_select="rerun",
                selection_mode="single-row",
                key="lm_table",
                column_config={
                    "utilisation": st.column_config.ProgressColumn(
                        "utilisation", min_value=0.0, max_value=1.5, format="percent"
                    ),
                    "warning at": st.column_config.NumberColumn("warning at", format="percent"),
                    "hierarchy": st.column_config.TextColumn("hierarchy", width="large"),
                    "scope": st.column_config.TextColumn("scope", width="medium"),
                    "type": st.column_config.TextColumn("type", width="medium"),
                },
            )
            picked = getattr(getattr(event, "selection", None), "rows", None) or []
            if picked:
                r = rows[picked[0]]
                with st.container(border=True):
                    st.markdown(f"**{r['limit_id']}** · {r['limit_type']} · {r['path']}")
                    d1, d2, d3 = st.columns(3)
                    d1.markdown(
                        f"- base amount: {_lim_amt(r['amount'], r['unit'])}\n"
                        f"- in force on the run: {_lim_amt(r['effective_amount'], r['unit'])}"
                        + (f" (increase `{r['increase_id']}`)" if r["increase_id"] else "")
                        + f"\n- warning threshold: {r['warning_threshold']:.0%}\n"
                        f"- scope: {r['filters'] or 'whole node'}"
                    )
                    d2.markdown(
                        f"- owner: {r['owner']}\n- approver: {r['approver'] or '—'}\n"
                        f"- approval: {r['approval_status']}\n"
                        f"- effective: {r['effective_from']} → {r['effective_to'] or 'open'}"
                    )
                    d3.markdown(
                        f"- run status: {badge.get(r['status'], '⚪')} {r['status']}\n"
                        f"- current: {_lim_amt(r['current'], r['unit'])}\n"
                        f"- utilisation: {r['utilisation']:.0%}\n"
                        if r["utilisation"] is not None
                        else f"- run status: {r['status']}\n"
                    )
                    if r["trades_in_scope"] is not None:
                        d3.markdown(f"- trades in scope: {r['trades_in_scope']:,}")
                    if r["rationale"]:
                        st.caption(f"Rationale: {r['rationale']}")
    with tab_u:
        u_rows = lh["rows"]
        u1, u2 = st.columns([2, 3])
        u_status = u1.multiselect(
            "Status",
            ["BREACH", "WARNING", "OK", "NO_DATA", "NO_RUN"],
            default=["BREACH", "WARNING"],
            key="lm_ustatus",
        )
        u_q = u2.text_input("Filter", placeholder="limit id, node, type, owner", key="lm_ufilter")
        u_q = u_q.strip().lower()
        u_rows = [
            r
            for r in u_rows
            if r["status"] in u_status
            and (not u_q or u_q in " ".join(str(v) for v in r.values() if v is not None).lower())
        ]
        u_rows.sort(key=lambda r: -(r["utilisation"] or 0))
        st.caption(
            f"{len(u_rows)} of {len(lh['rows'])} limits, highest utilisation first · "
            f"amounts in {ccy} millions, shares as fractions · ↑ marks a temporary increase in force"
        )
        u_table = pd.DataFrame(
            [
                {
                    "status": f"{badge.get(r['status'], '⚪')} {r['status']}",
                    "limit": r["limit_id"],
                    "type": r["limit_type"],
                    "hierarchy": r["path"],
                    "scope": r["filters"],
                    "current": _lim_amt(r["current"], r["unit"]),
                    "limit amount": _lim_amt(r["effective_amount"], r["unit"])
                    + (" ↑" if r["increase_id"] else ""),
                    "utilisation": r["utilisation"],
                    "owner": r["owner"],
                    "trades in scope": r["trades_in_scope"],
                }
                for r in u_rows
            ]
        )
        if u_table.empty:
            st.success("No limits in the selected statuses.")
        else:
            st.dataframe(
                u_table,
                use_container_width=True,
                hide_index=True,
                height=min(38 * (len(u_table) + 1) + 4, 900),
                column_config={
                    "utilisation": st.column_config.ProgressColumn(
                        "utilisation", min_value=0.0, max_value=1.5, format="percent"
                    ),
                    "hierarchy": st.column_config.TextColumn("hierarchy", width="large"),
                    "type": st.column_config.TextColumn("type", width="medium"),
                },
            )
    with tab_b:
        st.caption("The breach workflow stays on the Breaches page until it moves into this module.")
    with tab_i:
        st.caption("Temporary limit increases stay on the Breaches page until they move into this module.")

elif page == "Trade extract":
    header("Trade extract")
    st.caption(
        "Every trade of the selected run with its valuation and instrument terms. Filter, preview, "
        "then download the whole selection as CSV. The file names the run, so it can be reproduced."
    )
    opts = load("trade_extract_options", run_id)
    dims = opts["dims"]
    dim_labels = {
        "business_id": "Business",
        "desk_id": "Desk",
        "book_id": "Book",
        "legal_entity_id": "Legal entity",
        "trader_id": "Trader",
        "asset_class": "Asset class",
        "product_type": "Product type",
        "currency": "Currency",
        "direction": "Direction",
        "status": "Status",
        "clearing": "Clearing",
        "counterparty_id": "Counterparty",
        "netting_set_id": "Netting set",
    }
    filters: dict = {}
    with st.expander("Hierarchy and product", expanded=True):
        cols = st.columns(4)
        for i, d in enumerate([k for k in dim_labels if k in dims]):
            pick = cols[i % 4].multiselect(dim_labels[d], dims[d], key=f"tx_{d}")
            if pick:
                filters[d] = pick
    with st.expander("Dates and size", expanded=False):
        d1, d2, d3, d4, d5, d6 = st.columns(6)
        td = opts["dates"].get("trade_date", {})
        md = opts["dates"].get("maturity_date", {})
        iso = "YYYY-MM-DD"
        f_from = d1.text_input("Trade date from", value="", placeholder=td.get("min") or iso, key="tx_tdf")
        f_to = d2.text_input("Trade date to", value="", placeholder=td.get("max") or iso, key="tx_tdt")
        m_from = d3.text_input("Maturity from", value="", placeholder=md.get("min") or iso, key="tx_mf")
        m_to = d4.text_input("Maturity to", value="", placeholder=md.get("max") or iso, key="tx_mt")
        min_pv = d5.number_input(f"Min |PV| ({ccy} m)", min_value=0.0, value=0.0, step=0.5, key="tx_pv")
        min_qty = d6.number_input("Min |quantity|", min_value=0.0, value=0.0, step=1.0, key="tx_qty")
        for k, v in (
            ("trade_date_from", f_from.strip()),
            ("trade_date_to", f_to.strip()),
            ("maturity_from", m_from.strip()),
            ("maturity_to", m_to.strip()),
        ):
            if v:
                filters[k] = v
        if min_pv:
            filters["min_abs_pv"] = min_pv * M
        if min_qty:
            filters["min_abs_quantity"] = min_qty
    with st.expander("Text and trade ids", expanded=False):
        t1, t2 = st.columns([1, 2])
        q = t1.text_input("Search", placeholder="trade id, instrument id or description", key="tx_q")
        q = q.strip()
        ids_text = t2.text_area(
            "Trade ids (comma, space or line separated)",
            height=80,
            placeholder="IRS_000201 IRS_000202",
            key="tx_ids",
        )
        if q:
            filters["q"] = q
        ids = [x for x in ids_text.replace(",", " ").split() if x]
        if ids:
            filters["trade_ids"] = ids

    hashable = {k: tuple(v) if isinstance(v, list) else v for k, v in filters.items()}
    ext = load("trade_extract", run_id, **hashable)
    rows = ext["rows"]
    s1, s2, s3 = st.columns(3)
    s1.metric("Trades selected", f"{ext['count']:,}")
    s2.metric("PV of selection", money(ext["pv_total"], digits=2))
    s3.metric("Columns", len(ext["columns"]))
    st.download_button(
        f"Download {ext['count']:,} trades as CSV",
        data=client.trade_extract_csv(run_id, **filters),
        file_name=f"trades_{ext['business_date']}_{ext['run_id']}.csv",
        mime="text/csv",
        disabled=ext["count"] == 0,
        key="tx_download",
    )
    if rows:
        preview = pd.DataFrame(rows, columns=ext["columns"]).head(200)
        st.caption(f"Preview of the first {len(preview)} rows; the file carries all {ext['count']:,}.")
        st.dataframe(
            preview, use_container_width=True, hide_index=True, height=min(38 * (len(preview) + 1) + 4, 700)
        )
    else:
        st.info("No trades match the filters.")
