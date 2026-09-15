"""Templated drafts for the scripted provider: the same evidence a Claude-backed provider
would receive, rendered by fixed templates so the platform demos without credentials."""

from __future__ import annotations

from typing import Any


def _m(x: Any) -> str:
    return "n/a" if x is None else f"{float(x):,.2f}m"


def templated_draft(kind: str, ev: dict[str, Any]) -> str:
    fn = {
        "BREACH_INVESTIGATION": _breach,
        "SCENARIO_SUGGESTION": _scenarios,
        "MODEL_VALIDATION": _validation,
        "CSA_INGESTION": _ingestion,
    }.get(kind)
    text = fn(ev) if fn else f"No template for {kind}.\n\nEvidence keys: {', '.join(ev)}"
    return text + "\n\n_Scripted provider: numbers are read from the evidence, the wording is templated._"


def _breach(ev: dict[str, Any]) -> str:
    b, rem, hist = ev["breach"], ev.get("remediation") or {}, ev.get("utilisation_history") or []
    lines = [
        f"### Breach investigation: {b['limit_id']} ({b['breach_id']})",
        "",
        f"{b['limit_id']} on {b['level'].lower()} {b['entity_id']} has been breaching since {b['first_date']} "
        f"({b['consecutive_days']} consecutive runs): utilisation {b['first_utilisation']:.0%} at first, "
        f"{b['latest_utilisation']:.0%} on the latest run, peak {b['peak_utilisation']:.0%}. "
        f"Status {b['status']}, owner {b['owner']}"
        + (f", escalated to {b['escalated_to']}" if b.get("escalated_to") else "")
        + ".",
    ]
    if hist:
        lines.append("")
        lines.append(
            "Utilisation by run: "
            + ", ".join(f"{h['business_date']} {h['utilisation']:.0%}" for h in hist)
            + "."
        )
    tops = ev.get("top_contributors") or []
    if tops:
        lines += ["", f"Carried by ({ev.get('measure')}):"]
        for c in tops[:6]:
            share = (
                f" ({c['share_of_total']:.0%} of the total)" if c.get("share_of_total") is not None else ""
            )
            lines.append(
                f"- {c['trade_id']} ({c['product_type']}, {c['book_id']}, trader {c['trader_id']}, "
                f"counterparty {c['counterparty_id']}): {c['contribution']:,.0f}{share}"
            )
    ch = ev.get("changed_since_open") or {}
    if ch.get("trades_added_in_scope") or ch.get("trades_removed_from_scope"):
        lines += [
            "",
            f"Since the breach opened: {len(ch.get('trades_added_in_scope', []))} trades added to the scope "
            f"({', '.join(ch.get('trades_added_in_scope', [])[:5])}), "
            f"{len(ch.get('trades_removed_from_scope', []))} removed.",
        ]
    if rem:
        lines.append("")
        if "reduce_to_limit" in rem:
            lines.append(
                f"Remediation sized by the engine: {rem['measure']} is {rem['current']:,.0f} against a limit of "
                f"{rem['limit']:,.0f}; {rem['reduce_to_limit']:,.0f} has to come off to be inside the limit and "
                f"{rem['reduce_to_warning']:,.0f} to be back under the warning threshold. Unwinding "
                f"{', '.join(rem.get('unwind_candidates', []))} would remove {rem.get('unwind_candidates_cover', 0):,.0f}."
            )
        else:
            lines.append(
                f"Remediation: the {rem['measure']} share is {rem['current_share']:.0%} against {rem['limit_share']:.0%}; "
                f"reducing {', '.join(rem.get('unwind_candidates', []))} brings the share down fastest."
            )
    inc = ev.get("increase_requests") or []
    if inc:
        lines += [
            "",
            "Temporary increases: "
            + "; ".join(
                f"{i['increase_id']} {i['status']} to {i['new_amount']:,.0f} until {i['expires_on']}"
                for i in inc
            )
            + ".",
        ]
    lines += [
        "",
        "Recommended actions:",
        "1. Owner to confirm whether the position is intentional and give a reduction plan with dates.",
        "2. If the exposure is to stay, request a temporary increase with an expiry through the approval matrix.",
        "3. Re-check on the next run; the breach auto-escalates if it stays open.",
    ]
    return "\n".join(lines)


def _scenarios(ev: dict[str, Any]) -> str:
    lines = [f"### Suggested scenarios for {ev['business_date']} (run {ev['run_id']})", ""]
    exp = ev.get("largest_exposures") or []
    if exp:
        lines.append(
            "Largest exposures: "
            + ", ".join(f"{e['underlying']} {e['measure']} {e['value']:,.0f}" for e in exp[:5])
            + "."
        )
    mv = ev.get("largest_recent_moves") or []
    if mv:
        lines.append(
            "Largest recent moves: "
            + ", ".join(f"{m['underlying']} {m['sigma']:.1f} sigma on {m['date']}" for m in mv[:4])
            + "."
        )
    if ev.get("library_worst"):
        w = ev["library_worst"]
        lines.append(f"Worst standing library scenario: {w['scenario_id']} at {_m(w['total_pnl_m'])}.")
    lines.append("")
    for i, p in enumerate(ev.get("proposals") or [], 1):
        if "error" in p:
            lines.append(f"{i}. {p['name']}: could not run ({p['error']}).")
            continue
        by = ", ".join(
            f"{k} {_m(v)}" for k, v in sorted(p["by_asset_class_m"].items(), key=lambda kv: kv[1] or 0)[:3]
        )
        worst = ", ".join(f"{t['trade_id']} {_m(t['pnl_m'])}" for t in p["worst_trades"][:3])
        lines.append(
            f"{i}. **{p['name']}** — {p['why']}. Engine result {_m(p['total_pnl_m'])}; by asset class {by}; worst trades {worst}."
        )
    props = [p for p in ev.get("proposals") or [] if p.get("total_pnl_m") is not None]
    if props:
        worst = props[0]
        lines += [
            "",
            f"Candidate for the standing library: {worst['name']} (largest loss, {_m(worst['total_pnl_m'])}).",
        ]
    return "\n".join(lines)


def _validation(ev: dict[str, Any]) -> str:
    live = ev.get("live_evidence") or {}
    lines = ["# Model validation report (draft)", ""]
    if live:
        lines.append(
            f"Evidence run {live.get('run_id')} of {live.get('business_date')}: VaR {_m(live.get('var_m'))}, challenger "
            f"{_m(live.get('challenger_var_m'))}, Monte Carlo {_m(live.get('monte_carlo_var_m'))}; backtest zone "
            f"{live.get('backtest_zone')} with {live.get('backtest_exceptions')} exceptions over {live.get('backtest_days')} days; "
            f"data-quality verdict {live.get('dq_verdict')}."
        )
        if live.get("reconciliation"):
            r = live["reconciliation"]
            lines.append(
                f"Independent challenger ({r.get('vendor')}): gap {_m(r.get('gap_m'))} ({(r.get('gap_pct') or 0):+.1%}), attributed to {', '.join(f'{k} {_m(v)}' for k, v in (r.get('attribution_m') or {}).items())}."
            )
    lines.append("")
    lines.append(
        "Tests were executed."
        if ev.get("tests_executed")
        else "Tests were checked for presence, not executed."
    )
    for r in ev.get("records") or []:
        tests = r.get("tests") or []
        present = [t for t in tests if t["in_suite"]]
        missing = [t["name"] for t in tests if not t["in_suite"]]
        failed = [t["name"] for t in tests if t["result"] == "FAILED"]
        if failed:
            rec = "return: failing validation test(s) " + ", ".join(failed)
        elif missing:
            rec = "approve with conditions: named tests missing from the suite (" + ", ".join(missing) + ")"
        elif not tests:
            rec = "approve with conditions: no validation test named in the record"
        else:
            rec = "approve" + ("" if ev.get("tests_executed") else " once the named tests are executed")
        lines += [
            "",
            f"### {r['record_id']} {r['title']} (v{r['version']}, {r['status']})",
            f"Owner {r['owner']}; code `{r['code']}`; last validated {r['last_validated']}.",
            f"Validation tests: {len(present)} present"
            + (f" ({', '.join(t['name'] + ' ' + t['result'] for t in present)})" if present else "")
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + ".",
            f"Declared limitations: {r['limitations'][:300].strip() or 'none stated'}",
            f"Recommendation: {rec}.",
        ]
    return "\n".join(lines)


def _ingestion(ev: dict[str, Any]) -> str:
    f = ev["fields"]
    p = ev["proposal"]
    lines = [f"### CSA ingestion review: {ev['document']}", ""]
    read = [k for k, v in f.items() if v["found"]]
    dflt = [k for k, v in f.items() if not v["found"] and v["source"] == "default"]
    lines.append(f"{len(read)} fields read from the document, {len(dflt)} defaulted.")
    for k, v in f.items():
        if v["found"]:
            lines.append(f'- {k}: {v["value"]} — from "{v["source"][:80]}"')
    if dflt:
        lines.append("- defaults: " + ", ".join(f"{k}={f[k]['value']}" for k in dflt))
    lines += [
        "",
        f"Matching: counterparty {ev['matching']['counterparty']}; legal entity {ev['matching']['legal_entity']}.",
    ]
    if ev.get("warnings"):
        lines += ["", "Warnings:"] + [f"- {w}" for w in ev["warnings"]]
    ns, csa = p["netting_set"], p["csa"]
    lines += [
        "",
        f"Proposal ({'valid' if ev.get('valid') else 'NOT valid'}): netting set {ns['netting_set_id']} for {ns['counterparty_id']} "
        f"with {ns['legal_entity_id']} under {ns['agreement_type']}; CSA {csa['csa_id']} in {csa['collateral_currency']}: "
        f"thresholds we post {csa['threshold_we_post']:,.0f} / they post {csa['threshold_they_post']:,.0f}, MTA "
        f"{csa['minimum_transfer_amount']:,.0f}, IA {csa['independent_amount']:,.0f}, rounding {csa['rounding']:,.0f}, "
        f"haircut {csa['haircut']:.1%}, MPoR {csa['margin_period_of_risk_days']} days, {csa['call_frequency']} calls.",
        "",
        "Approver to confirm the counterparty match, the direction of each threshold and the eligible collateral before accepting.",
    ]
    return "\n".join(lines)
