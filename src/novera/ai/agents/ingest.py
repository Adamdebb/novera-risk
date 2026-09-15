"""ISDA/CSA document ingestion agent (AI-003).

A credit support annex term sheet (plain text or Markdown; PDF when ``pypdf`` is installed)
is parsed into a proposed netting set and CSA. Extraction is deterministic (labelled-field
patterns), so the same document always yields the same proposal, and every field carries
where it came from: a quoted line of the document, or a platform default. The proposal is a
review step: nothing is saved until an approver accepts it, and the approval is an audit
event. The provider drafts the review note that accompanies the proposal.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from novera.ai.agents.base import Agent, AgentNote
from novera.domain.counterparties import CSA, NettingSet
from novera.storage.duckdb_repository import DuckDBRepository
from novera.workflows.runs import AuditEvent

_NUM = r"(?:[A-Z]{3}\s+)?([0-9][0-9,\.]*)\s*(m|mm|million|k|bn|billion)?"
FIELDS: dict[str, tuple[str, ...]] = {
    "counterparty": (r"party b\s*[:\-]\s*(.+)", r"counterparty\s*[:\-]\s*(.+)"),
    "our_entity": (r"party a\s*[:\-]\s*(.+)", r"our entity\s*[:\-]\s*(.+)"),
    "agreement": (r"(?:governing |master )?agreement\s*[:\-]\s*(.+)",),
    "collateral_currency": (r"(?:base|collateral|eligible) currency\s*[:\-]\s*([A-Z]{3})",),
    "threshold_we_post": (r"threshold[^\n]*party a\)?\s*[:\-]\s*" + _NUM,),
    "threshold_they_post": (r"threshold[^\n]*party b\)?\s*[:\-]\s*" + _NUM,),
    "minimum_transfer_amount": (r"minimum transfer amount\s*[:\-]\s*" + _NUM, r"\bmta\s*[:\-]\s*" + _NUM),
    "independent_amount": (
        r"independent amount[^\n]*party b\)?\s*[:\-]\s*" + _NUM,
        r"independent amount\s*[:\-]\s*" + _NUM,
    ),
    "rounding": (r"rounding(?: amount)?\s*[:\-]\s*" + _NUM,),
    "haircut": (r"(?:valuation percentage|haircut)\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?)\s*%",),
    "margin_period_of_risk_days": (
        r"(?:margin period of risk|mpor)\s*[:\-]\s*([0-9]+)\s*(?:business )?days?",
    ),
    "call_frequency": (
        r"(?:valuation date|call frequency|frequency of margin calls?)\s*[:\-]\s*(daily|weekly|monthly)",
    ),
}
DEFAULTS: dict[str, Any] = {
    "agreement": "ISDA",
    "collateral_currency": "USD",
    "threshold_we_post": 0.0,
    "threshold_they_post": 0.0,
    "minimum_transfer_amount": 0.0,
    "independent_amount": 0.0,
    "rounding": 0.0,
    "haircut": 0.0,
    "margin_period_of_risk_days": 10,
    "call_frequency": "DAILY",
}
_MULT = {"m": 1e6, "mm": 1e6, "million": 1e6, "k": 1e3, "bn": 1e9, "billion": 1e9}


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PDF ingestion needs the pypdf package") from e
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text()


def _amount(groups: tuple[str, ...]) -> float:
    n = float(groups[0].replace(",", ""))
    unit = (groups[1] or "").lower() if len(groups) > 1 and groups[1] else ""
    return n * _MULT.get(unit, 1.0)


def extract_fields(text: str) -> dict[str, dict[str, Any]]:
    """Field -> {value, source, found}. Source is the matched line or 'default'."""
    out: dict[str, dict[str, Any]] = {}
    for field, patterns in FIELDS.items():
        found = None
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                found = m
                break
        if found is None:
            if field in DEFAULTS:
                out[field] = {"value": DEFAULTS[field], "source": "default", "found": False}
            else:
                out[field] = {"value": None, "source": "not found", "found": False}
            continue
        line_start = text.rfind("\n", 0, found.start()) + 1
        line_end = text.find("\n", found.start())
        line = text[line_start : line_end if line_end != -1 else len(text)]
        if field in (
            "threshold_we_post",
            "threshold_they_post",
            "minimum_transfer_amount",
            "independent_amount",
            "rounding",
        ):
            value: Any = _amount(found.groups())
        elif field == "haircut":
            value = float(found.group(1)) / 100.0
        elif field == "margin_period_of_risk_days":
            value = int(found.group(1))
        elif field == "call_frequency":
            value = found.group(1).upper()
        elif field == "collateral_currency":
            value = found.group(1).upper()
        else:
            value = found.group(1).strip()
        out[field] = {"value": value, "source": line.strip(), "found": True}
    return out


def _match_counterparty(name: str | None, counterparties: list) -> tuple[str | None, str]:
    if not name:
        return None, "no counterparty named in the document"
    key = name.lower()
    for c in counterparties:
        if c.counterparty_id.lower() == key or c.name.lower() == key:
            return c.counterparty_id, f"exact match on {c.name}"
    for c in counterparties:
        if c.name.lower() in key or key in c.name.lower():
            return c.counterparty_id, f"name match on {c.name}"
    return None, f"no counterparty in reference data matches '{name}'"


def _match_entity(name: str | None, org) -> tuple[str | None, str]:
    if not name:
        return None, "no Party A named"
    key = name.lower()
    for le in org.legal_entities:
        if (
            le.legal_entity_id.lower() == key
            or le.name.lower() == key
            or le.name.lower() in key
            or key in le.name.lower()
        ):
            return le.legal_entity_id, f"match on {le.name}"
    return None, f"no legal entity matches '{name}'"


class CSAIngestor(Agent):
    kind = "CSA_INGESTION"
    instructions = (
        "Write the review note for this proposed netting set and CSA: which fields were read from the "
        "document (quote the source line briefly), which fall back to defaults, any warnings (unmatched "
        "counterparty or entity, missing thresholds), and what the approver should confirm before "
        "accepting. Then list the proposal as it would be saved."
    )

    def gather(self, path: str = "", firm_id: str | None = None, **_: Any) -> dict[str, Any]:
        p = Path(path)
        text = read_document(p)
        fields = extract_fields(text)
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            cps = repo.load_counterparties()
            org = repo.load_organisation(firm_id or repo.list_firm_ids()[0])
            existing, csas = repo.load_netting_sets()
        cid, cp_note = _match_counterparty(fields["counterparty"]["value"], cps)
        le, le_note = _match_entity(fields["our_entity"]["value"], org)
        warnings = []
        if cid is None:
            warnings.append(cp_note)
        if le is None:
            warnings.append(le_note)
        for f in ("threshold_they_post", "minimum_transfer_amount", "collateral_currency"):
            if not fields[f]["found"]:
                warnings.append(f"{f} not found in the document; default {DEFAULTS[f]} proposed")
        already = [n.netting_set_id for n in existing if n.counterparty_id == cid and n.legal_entity_id == le]
        if already:
            warnings.append(
                f"a netting set already exists for this pair: {', '.join(already)} (it would be replaced)"
            )
        ns_id = f"NS_{cid}_{le}" if cid and le else None
        csa_id = f"CSA_{cid}_{le}" if cid and le else None
        proposal = {
            "netting_set": {
                "netting_set_id": ns_id,
                "counterparty_id": cid,
                "legal_entity_id": le,
                "agreement_type": str(fields["agreement"]["value"]).split()[0].upper()
                if fields["agreement"]["value"]
                else "ISDA",
                "csa_id": csa_id,
            },
            "csa": {
                "csa_id": csa_id,
                "collateral_currency": fields["collateral_currency"]["value"],
                "threshold_we_post": fields["threshold_we_post"]["value"],
                "threshold_they_post": fields["threshold_they_post"]["value"],
                "minimum_transfer_amount": fields["minimum_transfer_amount"]["value"],
                "independent_amount": fields["independent_amount"]["value"],
                "rounding": fields["rounding"]["value"],
                "haircut": fields["haircut"]["value"],
                "margin_period_of_risk_days": fields["margin_period_of_risk_days"]["value"],
                "call_frequency": fields["call_frequency"]["value"],
            },
        }
        valid = True
        try:
            if ns_id:
                NettingSet(**proposal["netting_set"])
                CSA(**proposal["csa"])
            else:
                valid = False
        except Exception as e:  # noqa: BLE001
            valid = False
            warnings.append(f"proposal does not validate: {e}")
        return {
            "document": str(p),
            "characters": len(text),
            "fields": fields,
            "matching": {"counterparty": cp_note, "legal_entity": le_note},
            "warnings": warnings,
            "proposal": proposal,
            "valid": valid,
        }


def propose_csa(db_path: str, path: str, provider=None, persist: bool = True) -> AgentNote:
    agent = CSAIngestor(db_path, provider)
    evidence = agent.gather(path=path)
    return agent.run(str(path), persist=persist, evidence=evidence)


def approve_csa(db_path: str, note_id: str, actor: str) -> dict[str, Any]:
    """Save the proposal of a CSA_INGESTION note; recorded as an audit event by ``actor``."""
    if not actor.strip():
        raise ValueError("an approver is required")
    with DuckDBRepository(db_path) as repo:
        notes = [n for n in repo.load_agent_notes(kind="CSA_INGESTION", limit=500) if n["note_id"] == note_id]
        if not notes:
            raise KeyError(f"note {note_id} not found")
        note = notes[0]
        ev = note["evidence"]
        if not ev.get("valid"):
            raise ValueError("the proposal is not valid; fix the document or the reference data first")
        ns = NettingSet(**ev["proposal"]["netting_set"])
        csa = CSA(**ev["proposal"]["csa"])
        repo.save_netting_sets([ns], [csa])
        repo.update_agent_note_status(note_id, "APPROVED")
        repo.save_audit_events(
            [
                AuditEvent.now(
                    actor,
                    "CSA_INGESTED",
                    ns.netting_set_id,
                    note_id=note_id,
                    document=ev.get("document"),
                    csa_id=csa.csa_id,
                    counterparty_id=ns.counterparty_id,
                )
            ]
        )
    return {"netting_set_id": ns.netting_set_id, "csa_id": csa.csa_id, "status": "APPROVED"}


def reject_csa(db_path: str, note_id: str, actor: str, reason: str = "") -> None:
    with DuckDBRepository(db_path) as repo:
        repo.update_agent_note_status(note_id, "REJECTED")
        repo.save_audit_events([AuditEvent.now(actor, "CSA_REJECTED", note_id, reason=reason)])
