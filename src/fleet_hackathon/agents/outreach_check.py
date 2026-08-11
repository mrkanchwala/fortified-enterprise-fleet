"""Outreach-Check Agent — monitors SLA-window compliance on simulated leads.

Deliberately has no capability beyond `ping_human` (capability.py's
OUTREACH_CHECK_SCOPE) — first contact is judged a relationship call, not a
technical one, so this agent is architecturally gated to escalation only. It
never holds a message-send function to begin with.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fleet_hackathon import model_armor
from fleet_hackathon.capability import OUTREACH_CHECK_SCOPE
from fleet_hackathon.config import COLLECTION_LEADS, DEFAULT_SLA_WINDOW_HOURS


@dataclass
class OutreachDecision:
    lead_id: str
    action: str | None  # "ping_human" or None
    reason: str


def decide(lead: dict, now: datetime) -> OutreachDecision:
    """Pure and deterministic — the unit-testable core, no LLM/network call."""
    lead_id = lead["lead_id"]
    if lead.get("status") == "escalated_to_human":
        return OutreachDecision(lead_id, None, "Already handed off to a team member — no further action")

    first_contact_ts = datetime.fromisoformat(lead["first_contact_ts"])
    sla_hours = lead.get("sla_window_hours", DEFAULT_SLA_WINDOW_HOURS)
    last_touch_ts = lead.get("last_touch_ts")
    reference_ts = datetime.fromisoformat(last_touch_ts) if last_touch_ts else first_contact_ts
    hours_since = (now - reference_ts).total_seconds() / 3600

    if hours_since <= sla_hours:
        return OutreachDecision(
            lead_id,
            None,
            f"Contacted {hours_since:.1f} hours ago — still within the {sla_hours}-hour window, no action needed",
        )
    return OutreachDecision(
        lead_id,
        "ping_human",
        f"No contact in {hours_since:.1f} hours — past the {sla_hours}-hour window, flagging for a team member",
    )


def run_cycle(
    db, gateway, audit_logger, lead_replies: dict[str, str] | None = None, now: datetime | None = None
) -> list[dict]:
    """One check cycle over all demo_leads. `lead_replies` optionally maps a
    lead_id to ingested free-text (e.g. a reply) — scanned through Model Armor
    before it ever reaches an LLM call, per the Fleet track's ingested-text
    security requirement.

    `now` (2026-08-10): defaults to real wall-clock time in production —
    injectable only so a test can pin a deterministic reference point instead
    of a seeded lead's "within SLA" assumption silently expiring as real
    calendar days pass since the test's fixture was written."""
    client = gateway.client_for("outreach_check")
    now = now or datetime.now(UTC)
    results = []

    for doc in db.collection(COLLECTION_LEADS).stream():
        lead = doc.to_dict()
        trace_id = audit_logger.new_trace_id()

        armor_note = None
        reply_text = (lead_replies or {}).get(lead["lead_id"])
        if reply_text:
            armor_result = model_armor.scan(reply_text)
            if armor_result.flagged:
                armor_note = (
                    f"Model Armor flagged ingested reply: pii={armor_result.pii_redacted}, "
                    f"injection_attempt={bool(armor_result.injection_flags)}"
                )

        decision = decide(lead, now)
        if decision.action is None:
            audit_logger.log(
                trace_id=trace_id,
                agent_name="outreach_check",
                action="no_action",
                status="ok",
                detail=decision.reason,
                success_criterion=OUTREACH_CHECK_SCOPE.success_criterion,
                attributes={"lead_id": decision.lead_id, "model_armor": armor_note},
            )
        else:
            client.call(decision.action, trace_id=trace_id, lead_id=decision.lead_id, reason=decision.reason)

        results.append({"lead_id": decision.lead_id, "action": decision.action, "reason": decision.reason})
    return results
