"""Alerts: what a risk manager must hear about, stored always and delivered when a channel
is configured. One alert per event, de-duplicated per business date."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.message import EmailMessage
from typing import Any, Protocol

from novera.config import Settings, get_settings
from novera.workflows.runs import new_run_id

SEVERITIES = ("INFO", "WARNING", "CRITICAL")


@dataclass
class Alert:
    alert_id: str
    at: datetime
    business_date: date
    severity: str
    kind: (
        str  # NEW_BREACH, AUTO_ESCALATION, BACK_WITHIN_LIMIT, RUN_VERDICT, RUN_FAILED, RUN_SUMMARY, RECON_GAP
    )
    subject: str  # limit id, run id
    title: str
    body: str
    recipients: list[str] = field(default_factory=list)
    run_id: str | None = None
    status: str = "STORED"  # STORED, SENT, PARTIAL, FAILED, SUPPRESSED
    deliveries: dict[str, str] = field(default_factory=dict)  # channel -> ok | error text

    @property
    def dedupe_key(self) -> str:
        return f"{self.kind}|{self.subject}|{self.business_date.isoformat()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "at": self.at.isoformat(),
            "business_date": self.business_date.isoformat(),
            "severity": self.severity,
            "kind": self.kind,
            "subject": self.subject,
            "title": self.title,
            "body": self.body,
            "recipients": self.recipients,
            "run_id": self.run_id,
            "status": self.status,
            "deliveries": self.deliveries,
        }


def _alert(
    kind: str,
    severity: str,
    subject: str,
    title: str,
    body: str,
    business_date: date,
    run_id: str | None,
    recipients: list[str],
) -> Alert:
    return Alert(
        new_run_id("alt"),
        datetime.now(UTC),
        business_date,
        severity,
        kind,
        subject,
        title,
        body,
        list(dict.fromkeys(r for r in recipients if r)),  # one line per role, in order
        run_id,
    )


def alerts_from_run(result: Any, sync: Any | None) -> list[Alert]:
    """Derive alerts from an EOD result and its breach sync outcome."""
    run = result.run
    bd, rid, m = run.business_date, run.run_id, 1e6
    out: list[Alert] = []
    if sync is not None:
        for b in sync.raised:
            out.append(
                _alert(
                    "NEW_BREACH",
                    "CRITICAL",
                    b.limit_id,
                    f"New breach: {b.limit_id}",
                    f"{b.limit_type} limit on {b.entity_id} at {b.latest_utilisation:.0%} of limit on "
                    f"{bd}. Owner {b.owner}. Acknowledge on the Breaches page.",
                    bd,
                    rid,
                    [b.owner],
                )
            )
        for b in sync.auto_escalated:
            out.append(
                _alert(
                    "AUTO_ESCALATION",
                    "CRITICAL",
                    b.limit_id,
                    f"Auto-escalated: {b.limit_id}",
                    f"Breach open for {b.consecutive_days} runs without resolution, now with "
                    f"{b.escalated_to}. Latest utilisation {b.latest_utilisation:.0%}.",
                    bd,
                    rid,
                    [b.escalated_to or "Head of Market Risk", b.owner],
                )
            )
        for b in sync.back_within_limit:
            out.append(
                _alert(
                    "BACK_WITHIN_LIMIT",
                    "INFO",
                    b.limit_id,
                    f"Back within limit: {b.limit_id}",
                    f"Utilisation {b.latest_utilisation:.0%}; the breach can be closed as RISK_REDUCED.",
                    bd,
                    rid,
                    [b.owner],
                )
            )
    if run.verdict != "GREEN":
        sev = "CRITICAL" if run.verdict == "RED" else "WARNING"
        codes = sorted({f.code for f in result.dq.findings})
        out.append(
            _alert(
                "RUN_VERDICT",
                sev,
                rid,
                f"Run verdict {run.verdict}",
                f"{len(result.dq.findings)} data-quality findings: {', '.join(codes)}. "
                + (
                    "Do not publish without an override."
                    if run.verdict == "RED"
                    else "Results usable with caveats."
                ),
                bd,
                rid,
                ["Market Risk Control"],
            )
        )
    sm = run.summary
    out.append(
        _alert(
            "RUN_SUMMARY",
            "INFO",
            rid,
            f"EOD run complete for {bd}",
            f"VaR {sm['var'] / m:,.1f}m, ES {sm['es'] / m:,.1f}m, worst stress {sm['worst_stress_name']} "
            f"{(sm['worst_stress'] or 0) / m:,.1f}m, {sm['breaches']} breaches, {sm['warnings']} "
            f"warnings, verdict {run.verdict}.",
            bd,
            rid,
            ["Market Risk"],
        )
    )
    return out


def run_failed_alert(business_date: date, error: str, attempt: int) -> Alert:
    return _alert(
        "RUN_FAILED",
        "CRITICAL",
        business_date.isoformat(),
        f"EOD run failed for {business_date}",
        f"Attempt {attempt}: {error[:800]}",
        business_date,
        None,
        ["Risk IT", "Market Risk Control"],
    )


# --- channels --------------------------------------------------------------------------------


class Channel(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...


class SlackWebhook:
    name = "slack"

    def __init__(self, url: str, client: Any | None = None) -> None:
        import httpx

        self.url = url
        self.client = client or httpx.Client(timeout=10)

    def send(self, alert: Alert) -> None:
        icon = {"CRITICAL": ":red_circle:", "WARNING": ":large_orange_circle:", "INFO": ":white_circle:"}[
            alert.severity
        ]
        text = (
            f"{icon} *{alert.title}*\n{alert.body}\n"
            f"_{alert.kind} · {alert.business_date} · to {', '.join(alert.recipients)}_"
        )
        r = self.client.post(self.url, json={"text": text})
        r.raise_for_status()


class Email:
    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        sender: str,
        to: list[str],
        smtp_factory: Any | None = None,
    ) -> None:
        self.host, self.port, self.user, self.password, self.sender, self.to = (
            host,
            port,
            user,
            password,
            sender,
            to,
        )
        self.smtp_factory = smtp_factory or (lambda: smtplib.SMTP(self.host, self.port, timeout=15))

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[{alert.severity}] {alert.title}"
        msg["From"], msg["To"] = self.sender, ", ".join(self.to)
        msg.set_content(
            f"{alert.body}\n\nKind: {alert.kind}\nBusiness date: {alert.business_date}\n"
            f"Run: {alert.run_id}\nRecipients: {', '.join(alert.recipients)}"
        )
        with self.smtp_factory() as smtp:
            if self.user and self.password:
                smtp.starttls()
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def channels_from_settings(s: Settings | None = None) -> list[Channel]:
    s = s or get_settings()
    out: list[Channel] = []
    if s.slack_webhook_url:
        out.append(SlackWebhook(s.slack_webhook_url))
    if s.smtp_host and s.alert_email_from and s.alert_email_to:
        out.append(
            Email(
                s.smtp_host,
                s.smtp_port,
                s.smtp_user,
                s.smtp_password,
                s.alert_email_from,
                [x.strip() for x in s.alert_email_to.split(",") if x.strip()],
            )
        )
    return out


# --- dispatch --------------------------------------------------------------------------------


def dispatch(
    repo: Any, alerts: list[Alert], channels: list[Channel], min_severity: str = "WARNING"
) -> list[Alert]:
    """Store every alert; deliver those at or above ``min_severity`` through each channel.
    Duplicates (same kind, subject and business date already stored) are suppressed."""
    existing = {a["dedupe_key"] for a in repo.load_alerts(limit=5000)}
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    for a in alerts:
        if a.dedupe_key in existing:
            a.status = "SUPPRESSED"
        elif channels and rank[a.severity] >= rank[min_severity]:
            for ch in channels:
                try:
                    ch.send(a)
                    a.deliveries[ch.name] = "ok"
                except Exception as e:  # noqa: BLE001 - delivery failures are recorded, never raised
                    a.deliveries[ch.name] = f"{type(e).__name__}: {e}"[:300]
            oks = [v == "ok" for v in a.deliveries.values()]
            a.status = "SENT" if all(oks) else ("PARTIAL" if any(oks) else "FAILED")
        repo.save_alert({**a.to_dict(), "dedupe_key": a.dedupe_key})
        existing.add(a.dedupe_key)
    return alerts
