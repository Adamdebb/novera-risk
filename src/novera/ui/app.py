"""Streamlit thin client: the morning risk dashboard and its drill-downs.

Holds no business logic. Every number on screen comes from the client (local service or
HTTP API) and carries the run id it was computed in (ADR 0004).
"""

from __future__ import annotations

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
        ["Overview", "Drill-down", "VaR", "Stress", "Limits", "P&L explain", "Data quality", "Runs & audit"],
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
