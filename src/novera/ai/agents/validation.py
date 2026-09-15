"""Model-validation report drafting agent (AI-002).

Evidence per methodology record in ``docs/methodology``: the record's metadata (version,
owner, status, code), its stated validation tests and whether each still exists in the test
suite (optionally executed), plus the live evidence a validator would ask for from the
latest run: backtest zone and p-values, challenger and Monte Carlo VaR against the official
number, reconciliation gap against the independent feed, P&L attribution zones, data-quality
verdict. The provider drafts one assessment per record; the report is written to
``data/reports/model_validation_<date>.md`` and stored as an agent note.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from novera.ai.agents.base import Agent, AgentNote, _m
from novera.storage.duckdb_repository import DuckDBRepository

TEST_RE = re.compile(r"`?(test_[a-z0-9_]+)`?")


def methodology_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "methodology").is_dir():
            return parent / "docs" / "methodology"
    return Path("docs/methodology")


def parse_record(path: Path) -> dict[str, Any]:
    text = path.read_text()
    title = text.splitlines()[0].lstrip("# ").strip()
    m = re.search(r"\(ID:\s*([A-Z]+-\d+)\)", title)
    rid = m.group(1) if m else path.stem.split("-")[0] + "-" + path.stem.split("-")[1]
    meta = dict(re.findall(r"^\| ([A-Za-z ]+) \| (.+?) \|$", text, flags=re.M))
    sections: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = ""
        elif cur:
            sections[cur] += line + "\n"
    tests = sorted(set(TEST_RE.findall(sections.get("Validation tests", ""))))
    return {
        "record_id": rid,
        "title": title.split("(ID:")[0].strip(),
        "file": path.name,
        "version": meta.get("Version", ""),
        "owner": meta.get("Owner", ""),
        "status": meta.get("Approval status", ""),
        "code": meta.get("Code", "").strip("`"),
        "last_validated": meta.get("Last validated", ""),
        "limitations": next((v.strip()[:800] for k, v in sections.items() if "imitation" in k), ""),
        "tests": tests,
    }


def _tests_in_suite(root: Path) -> set[str]:
    names: set[str] = set()
    for p in (root / "tests").glob("test_*.py"):
        names |= set(re.findall(r"^def (test_[a-z0-9_]+)", p.read_text(), flags=re.M))
    return names


def _run_tests(root: Path, names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", " or ".join(names), "tests"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=1800,
            env={"NOVERA_WORKERS": "1", **__import__("os").environ},
        )
    except subprocess.TimeoutExpired:
        return dict.fromkeys(names, "TIMEOUT")
    out = proc.stdout + proc.stderr
    status: dict[str, str] = {}
    for n in names:
        if re.search(rf"FAILED .*::{n}\b", out):
            status[n] = "FAILED"
        elif re.search(rf"{n}\b", out) or proc.returncode == 0:
            status[n] = "PASSED" if proc.returncode == 0 else "NOT RUN"
        else:
            status[n] = "NOT RUN"
    return status


class ValidationDrafter(Agent):
    kind = "MODEL_VALIDATION"
    instructions = (
        "Draft a model-validation report. For each methodology record write a short assessment: what "
        "the model is, the evidence available (benchmark tests present or executed, backtest, challenger "
        "and Monte Carlo agreement, reconciliation, data quality), the limitations the record itself "
        "declares, and a recommendation (approve, approve with conditions, or return) with the conditions "
        "named. Do not invent test results: a test that was not executed is 'present, not executed'."
    )

    def gather(
        self, run_id: str | None = None, records: list[str] | None = None, run_tests: bool = False, **_: Any
    ) -> dict[str, Any]:
        mdir = methodology_dir()
        root = mdir.parent.parent
        recs = [parse_record(p) for p in sorted(mdir.glob("*-*.md")) if p.name != "TEMPLATE.md"]
        if records:
            wanted = {r.upper() for r in records}
            recs = [r for r in recs if r["record_id"] in wanted or r["record_id"].split("-")[0] in wanted]
        suite = _tests_in_suite(root)
        names = sorted({t for r in recs for t in r["tests"] if t in suite})
        executed = _run_tests(root, names) if run_tests else {}
        for r in recs:
            r["tests"] = [
                {
                    "name": t,
                    "in_suite": t in suite,
                    "result": executed.get(t, "NOT EXECUTED" if t in suite else "MISSING"),
                }
                for t in r["tests"]
            ]
        live: dict[str, Any] = {}
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            runs = repo.list_runs(run_type="EOD", limit=1)
            run = repo.load_run(run_id) if run_id else (runs[0] if runs else None)
            if run is not None:
                s = run.summary
                live = {
                    "run_id": run.run_id,
                    "business_date": str(run.business_date),
                    "dq_verdict": run.verdict,
                    "var_m": _m(s.get("var")),
                    "challenger_var_m": _m(s.get("challenger_var")),
                    "monte_carlo_var_m": _m(s.get("monte_carlo_var")),
                    "backtest_zone": s.get("backtest_zone"),
                    "backtest_exceptions": s.get("backtest_exceptions"),
                    "backtest_days": s.get("backtest_days"),
                    "regulatory": {
                        k: (_m(v) if isinstance(v, float) and abs(v) > 1e3 else v)
                        for k, v in (s.get("regulatory") or {}).items()
                    },
                }
                try:
                    bt = repo.load_run_frame(run.run_id, "backtest_summary")
                    live["backtest"] = [
                        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
                        for row in bt.to_dict("records")
                    ]
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from novera.api.service import RiskService

                    r0 = RiskService(repo).reconciliation(run.run_id)
                    if r0:
                        live["reconciliation"] = {
                            "vendor": r0.get("vendor"),
                            "gap_m": _m(r0.get("gap")),
                            "gap_pct": r0.get("gap_pct"),
                            "attribution_m": {k: _m(v) for k, v in (r0.get("attribution") or {}).items()},
                        }
                except Exception:  # noqa: BLE001
                    pass
        return {
            "run_id": live.get("run_id"),
            "records": recs,
            "tests_executed": bool(run_tests),
            "live_evidence": live,
        }


def draft_validation_report(
    db_path: str,
    run_id: str | None = None,
    records: list[str] | None = None,
    run_tests: bool = False,
    out_dir: Path | None = None,
    provider=None,
    persist: bool = True,
) -> tuple[AgentNote, Path | None]:
    agent = ValidationDrafter(db_path, provider)
    evidence = agent.gather(run_id=run_id, records=records, run_tests=run_tests)
    note = agent.run("model-validation", run_id=evidence.get("run_id"), persist=persist, evidence=evidence)
    path = None
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        date = (evidence.get("live_evidence") or {}).get("business_date") or "undated"
        path = out_dir / f"model_validation_{date}.md"
        path.write_text(note.text)
    return note, path
