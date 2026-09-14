"""Daily risk pack: HTML, PDF and Excel from a stored run. Record RP-001.

Every table comes from the run's stored frames through the same service the API uses.
The pack is stamped with the run id, snapshot ids and model versions on every page.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from novera.api.service import RiskService
from novera.config import get_settings
from novera.storage.duckdb_repository import DuckDBRepository

M = 1e6

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;margin:32px;font-size:12px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #e5e7eb;padding-bottom:3px}
.stamp{color:#6b7280;font-size:10px;margin-bottom:14px}
.kpis{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0 6px}
.kpi{border:1px solid #e5e7eb;border-radius:6px;padding:8px 12px;min-width:120px}
.kpi .l{color:#6b7280;font-size:10px}.kpi .v{font-size:18px;font-weight:600}
table{border-collapse:collapse;width:100%;margin:4px 0 10px;font-size:11px}
th,td{border-bottom:1px solid #f1f5f9;padding:3px 6px;text-align:right}th{background:#f8fafc;text-align:right}
td:first-child,th:first-child{text-align:left}
.BREACH{color:#b91c1c;font-weight:600}.WARNING{color:#b45309}.RED{color:#b91c1c}.AMBER{color:#b45309}.GREEN{color:#15803d}
.flag{margin:2px 0 2px 12px}.small{color:#6b7280;font-size:10px}
@media print{h2{page-break-after:avoid}table{page-break-inside:auto}tr{page-break-inside:avoid}}
"""


@dataclass
class PackFiles:
    html: Path
    xlsx: Path
    pdf: Path | None


def _m(x: Any, d: int = 1) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x / M:,.{d}f}m"


def _table(
    df: pd.DataFrame,
    money_cols: tuple[str, ...] = (),
    pct_cols: tuple[str, ...] = (),
    int_cols: tuple[str, ...] = (),
    max_rows: int = 40,
) -> str:
    if df is None or len(df) == 0:
        return "<p class='small'>none</p>"
    d = df.head(max_rows).copy()
    for c in money_cols:
        if c in d:
            d[c] = d[c].map(lambda x: _m(x, 2))
    for c in pct_cols:
        if c in d:
            d[c] = d[c].map(lambda x: "—" if pd.isna(x) else f"{x:.0%}")
    for c in int_cols:
        if c in d:
            d[c] = d[c].map(lambda x: "—" if pd.isna(x) else f"{int(x):,}")
    for c in d.columns:
        if d[c].dtype.kind == "f" and c not in money_cols and c not in pct_cols and c not in int_cols:
            d[c] = d[c].map(lambda x: "—" if pd.isna(x) else f"{x:,.3f}")
    d = d.where(pd.notna(d), "—")
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in d.columns)
    rows = []
    for _, r in d.iterrows():
        cells = []
        for c in d.columns:
            val = str(r[c])
            cls = f" class='{val}'" if val in ("BREACH", "WARNING", "RED", "AMBER", "GREEN") else ""
            cells.append(f"<td{cls}>{html.escape(val)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def build_pack(repo: DuckDBRepository, run_id: str | None, out_dir: Path, pdf: bool = True) -> PackFiles:
    s = get_settings()
    svc = RiskService(repo)
    r = svc.resolve(run_id)
    rid = r.run_id
    sm = r.summary
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"risk_pack_{r.business_date}_{rid}"

    # --- tables ------------------------------------------------------------------------------
    var_summary = pd.DataFrame(svc.var_summary(rid))
    var_by_ac = pd.DataFrame(svc.var_by("asset_class", rid))
    var_by_desk = pd.DataFrame(svc.var_by("desk_id", rid))
    stress = pd.DataFrame(svc.stress(rid, by=None))
    limits = pd.DataFrame(svc.limits(rid))
    breaches = pd.DataFrame(svc.breaches(open_only=True))
    dq = pd.DataFrame(svc.dq(rid))
    pnl = svc.pnl(rid, by="desk_id")
    pnl_steps = pd.DataFrame(pnl.get("steps", []))
    pnl_by_desk = pd.DataFrame(pnl.get("by", []))
    conc = repo.load_run_frame(rid, "concentration")
    conc_top = repo.load_run_frame(rid, "concentration_top")
    liq_b = repo.load_run_frame(rid, "liquidity_buckets")
    liq_d = repo.load_run_frame(rid, "liquidity_desks")
    flags = repo.load_run_frame(rid, "risk_flags")
    bt = repo.load_run_frame(rid, "backtest_summary")
    dv01 = pd.DataFrame(svc.sensitivities(rid, measure="DV01", by="desk_id"))
    positions = pd.DataFrame(svc.positions(rid, by="desk_id"))

    # --- HTML ---------------------------------------------------------------------------------
    stamp = (
        f"run {rid} · business date {r.business_date} · verdict {r.verdict} · portfolio "
        f"{r.portfolio_snapshot_id} · market {r.market_snapshot_id} · models "
        + ", ".join(f"{k} {v}" for k, v in r.model_versions.items())
        + f" · config {r.config_hash}"
    )
    kpis = [
        ("VaR 99% 1d", _m(sm["var"], 2)),
        ("ES 97.5%", _m(sm["es"], 2)),
        ("10d VaR", _m(sm["var_scaled"])),
        ("Challenger VaR", _m(sm.get("challenger_var"), 2)),
        ("Monte Carlo VaR", _m(sm.get("monte_carlo_var"), 2)),
        ("Liquidity-adjusted VaR", _m(sm.get("liquidity_adjusted_var"), 2)),
        ("Worst stress", f"{_m(sm['worst_stress'])} {sm['worst_stress_name']}"),
        ("P&L", _m(sm.get("pnl_total"), 2)),
        ("Breaches / warnings", f"{sm['breaches']} / {sm['warnings']}"),
        ("Data quality", f"<span class='{r.verdict}'>{r.verdict}</span> ({sm['dq_findings']} findings)"),
        (
            "Backtest",
            f"<span class='{sm.get('backtest_zone', '')}'>{sm.get('backtest_zone', '—')}</span> "
            f"({sm.get('backtest_exceptions', '—')} exceptions / {sm.get('backtest_days', '—')} days)",
        ),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='l'>{k}</div><div class='v'>{v}</div></div>" for k, v in kpis
    )
    steps_html = ""
    if len(pnl_steps):
        steps_html = ", ".join(
            f"{x['step']} {x['pnl'] / M:+.2f}" for _, x in pnl_steps.iterrows() if abs(x["pnl"]) > 1e4
        )
    flag_html = (
        "".join(
            f"<div class='flag'>• <b>{html.escape(str(f['kind']))}</b> {html.escape(str(f['message']))}</div>"
            for _, f in flags.iterrows()
        )
        if len(flags)
        else "<p class='small'>none</p>"
    )
    sections = [
        (
            "Value at Risk",
            _table(
                var_summary[["method", "var", "es", "var_scaled", "scenarios", "window_days"]],
                money_cols=("var", "es", "var_scaled"),
                int_cols=("scenarios", "window_days"),
            )
            + "<h3>By asset class</h3>"
            + _table(var_by_ac, money_cols=("component_var", "component_es"), int_cols=("trades",))
            + "<h3>By desk</h3>"
            + _table(var_by_desk, money_cols=("component_var", "component_es"), int_cols=("trades",)),
        ),
        (
            "Backtest",
            _table(
                bt[
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
                ]
                if len(bt)
                else bt,
                int_cols=("days", "exceptions"),
            ),
        ),
        (
            "Stress",
            _table(stress[["name", "kind", "total"]] if len(stress) else stress, money_cols=("total",)),
        ),
        (
            "Limits: breaches and warnings",
            _table(
                limits[limits["status"].isin(["BREACH", "WARNING"])][
                    ["status", "limit_id", "level", "entity_id", "current", "amount", "utilisation", "owner"]
                ]
                if len(limits)
                else limits,
                money_cols=("current", "amount"),
                pct_cols=("utilisation",),
            ),
        ),
        (
            "Open breaches",
            _table(
                breaches[
                    [
                        "status",
                        "limit_id",
                        "owner",
                        "first_date",
                        "consecutive_days",
                        "latest_utilisation",
                        "escalated_to",
                    ]
                ]
                if len(breaches)
                else breaches,
                pct_cols=("latest_utilisation",),
                int_cols=("consecutive_days",),
            ),
        ),
        (
            "P&L explain",
            f"<p>Total {_m(sm.get('pnl_total'), 2)}: {steps_html}</p>"
            + _table(pnl_by_desk, money_cols=tuple(c for c in pnl_by_desk.columns if c != "desk_id")),
        ),
        (
            "Data quality",
            _table(
                dq[["severity", "code", "subject", "message", "affected_trades", "owner"]] if len(dq) else dq,
                int_cols=("affected_trades",),
            ),
        ),
        (
            "Concentration",
            _table(
                conc[
                    [
                        "dimension",
                        "basis",
                        "groups",
                        "hhi",
                        "effective_number",
                        "top1_share",
                        "top5_share",
                        "largest",
                    ]
                ]
                if len(conc)
                else conc,
                pct_cols=("top1_share", "top5_share"),
                int_cols=("groups",),
            )
            + "<h3>Largest VaR contributors</h3>"
            + _table(
                conc_top[["trade_id", "desk_id", "product_type", "pv", "var_contribution", "share_of_var"]]
                if len(conc_top)
                else conc_top,
                money_cols=("pv", "var_contribution"),
                pct_cols=("share_of_var",),
                max_rows=15,
            ),
        ),
        (
            "Liquidity",
            f"<p>Liquidity-adjusted VaR {_m(sm.get('liquidity_adjusted_var'), 2)} versus VaR "
            f"{_m(sm['var'], 2)}; weighted liquidation horizon {sm.get('liquidity_horizon_days', 0):.1f} days.</p>"
            + _table(liq_b, money_cols=("abs_pv",), pct_cols=("share_of_abs_pv",), int_cols=("trades",))
            + "<h3>By desk</h3>"
            + _table(liq_d, money_cols=("bidask_cost",), int_cols=("trades",)),
        ),
        ("Risk flags", flag_html),
        (
            "DV01 ladder by desk (per bp)",
            _table(
                dv01.pivot_table(
                    index=["desk_id", "underlying"],
                    columns="bucket",
                    values="value",
                    aggfunc="sum",
                    fill_value=0.0,
                ).reset_index()
                if len(dv01)
                else dv01,
                max_rows=40,
            ),
        ),
        ("Positions by desk", _table(positions, money_cols=("pv",), int_cols=("trades", "unpriced"))),
    ]
    body = "".join(f"<h2>{t}</h2>{c}" for t, c in sections)
    page = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{s.platform_name} risk pack "
        f"{r.business_date}</title><style>{CSS}</style></head><body>"
        f"<h1>{s.platform_name} — Daily market risk pack — {r.business_date}</h1>"
        f"<div class='stamp'>{html.escape(stamp)}</div><div class='kpis'>{kpi_html}</div>{body}"
        f"<p class='small'>Generated by {s.platform_name}. All figures are read from stored run {rid}; "
        f"nothing is recomputed in this document.</p></body></html>"
    )
    html_path = stem.with_suffix(".html")
    html_path.write_text(page, encoding="utf-8")

    # --- Excel --------------------------------------------------------------------------------
    xlsx_path = stem.with_suffix(".xlsx")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
        pd.DataFrame(
            [
                {"key": k, "value": v}
                for k, v in {
                    **{"run_id": rid, "business_date": str(r.business_date), "verdict": r.verdict},
                    **sm,
                }.items()
                if not isinstance(v, dict)
            ]
        ).to_excel(xw, sheet_name="Summary", index=False)
        for name, df in (
            ("VaR", var_summary),
            ("VaR by asset class", var_by_ac),
            ("VaR by desk", var_by_desk),
            ("Backtest", bt),
            ("Stress", stress),
            ("Limits", limits),
            ("Breaches", breaches),
            ("PnL steps", pnl_steps),
            ("PnL by desk", pnl_by_desk),
            ("Data quality", dq),
            ("Concentration", conc),
            ("Top contributors", conc_top),
            ("Liquidity buckets", liq_b),
            ("Liquidity desks", liq_d),
            ("Flags", flags),
            ("DV01", dv01),
            ("Positions", positions),
        ):
            (df if len(df) else pd.DataFrame({"note": ["none"]})).to_excel(
                xw, sheet_name=name[:31], index=False
            )

    # --- PDF via headless Chromium (Playwright) when available -------------------------------
    pdf_path: Path | None = None
    if pdf:
        try:
            from playwright.sync_api import sync_playwright

            pdf_path = stem.with_suffix(".pdf")
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                pg.goto(html_path.resolve().as_uri())
                pg.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                )
                b.close()
        except Exception:  # noqa: BLE001 - PDF is optional; HTML and XLSX always exist
            pdf_path = None
    return PackFiles(html_path, xlsx_path, pdf_path)
