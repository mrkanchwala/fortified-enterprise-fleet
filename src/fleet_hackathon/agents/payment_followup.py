"""Payment-Followup Agent — tracks (simulated) invoice payment status with
graduated judgment: tone/frequency vary by days-overdue, escalating to a human
past a stated threshold instead of just repeating itself. This graduated
threshold logic (not a fixed cron repeat) is what makes it a decision-maker.

`force_action` exists only for the seeded demo drift scenario (see
runtime.py) — it deliberately makes the agent take the *wrong* action so the
Gateway's independent drift check (gateway.py's `_check_reminder_drift`) can
be shown catching it live. It is never used outside that one seeded record.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fleet_hackathon.capability import PAYMENT_FOLLOWUP_SCOPE
from fleet_hackathon.config import (
    COLLECTION_INVOICES,
    OVERDUE_ESCALATION_DAYS,
    OVERDUE_ESCALATION_REMINDER_COUNT,
)

GENTLE_FIRM_CUTOFF_DAYS = 10


@dataclass
class FollowupDecision:
    invoice_id: str
    action: str | None  # "send_reminder" | "escalate_to_human" | None
    tone: str | None
    reason: str


def decide(invoice: dict, now: datetime) -> FollowupDecision:
    """Pure and deterministic — the unit-testable core, no LLM/network call."""
    invoice_id = invoice["invoice_id"]
    if invoice.get("status") in ("escalated", "paid"):
        return FollowupDecision(invoice_id, None, None, f"Invoice already {invoice['status']} — nothing more to do")

    due_ts = datetime.fromisoformat(invoice["due_ts"])
    days_overdue = (now - due_ts).days
    reminders_sent = invoice.get("reminders_sent", 0)

    if days_overdue < 0:
        return FollowupDecision(invoice_id, None, None, f"Not due yet — {-days_overdue} days remaining")

    if days_overdue >= OVERDUE_ESCALATION_DAYS or reminders_sent >= OVERDUE_ESCALATION_REMINDER_COUNT:
        return FollowupDecision(
            invoice_id,
            "escalate_to_human",
            None,
            f"{days_overdue} days overdue with {reminders_sent} reminder(s) already sent — "
            "handing off to a team member instead of sending another reminder",
        )

    tone = "gentle" if days_overdue < GENTLE_FIRM_CUTOFF_DAYS else "firm"
    return FollowupDecision(
        invoice_id,
        "send_reminder",
        tone,
        f"{days_overdue} days overdue, {reminders_sent} reminder(s) sent so far — sending a {tone} reminder",
    )


def run_cycle(
    db, gateway, audit_logger, force_action: dict | None = None, now: datetime | None = None
) -> list[dict]:
    """`now` (2026-08-10, same fix as outreach_check.run_cycle): defaults to
    real wall-clock time in production; injectable only so a test can pin a
    deterministic reference point instead of a seeded invoice's overdue-day
    assumption silently drifting as real calendar days pass."""
    client = gateway.client_for("payment_followup")
    now = now or datetime.now(UTC)
    results = []

    for doc in db.collection(COLLECTION_INVOICES).stream():
        invoice = doc.to_dict()
        invoice_id = invoice["invoice_id"]
        trace_id = audit_logger.new_trace_id()

        forced = (force_action or {}).get(invoice_id)
        if forced:
            client.call(forced["action"], trace_id=trace_id, invoice_id=invoice_id, **forced.get("kwargs", {}))
            results.append({"invoice_id": invoice_id, "action": forced["action"], "reason": "forced (demo drift scenario)"})
            continue

        decision = decide(invoice, now)
        if decision.action is None:
            audit_logger.log(
                trace_id=trace_id,
                agent_name="payment_followup",
                action="no_action",
                status="ok",
                detail=decision.reason,
                success_criterion=PAYMENT_FOLLOWUP_SCOPE.success_criterion,
                attributes={"invoice_id": invoice_id},
            )
        elif decision.action == "send_reminder":
            client.call(
                "send_reminder",
                trace_id=trace_id,
                invoice_id=invoice_id,
                tone=decision.tone,
                message=f"Hi — this is a reminder that invoice {invoice_id} is overdue. Please arrange payment when you can.",
            )
        else:
            client.call("escalate_to_human", trace_id=trace_id, invoice_id=invoice_id, reason=decision.reason)

        results.append({"invoice_id": invoice_id, "action": decision.action, "reason": decision.reason})
    return results
