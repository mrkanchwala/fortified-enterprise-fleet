"""The real side-effecting functions the Gateway dispatches to.

Agents never import this module directly — only `gateway.Gateway` holds these
references. An agent's only path to any effect in the world is
`GatewayClient.call(action_name, **kwargs)`, checked against its declared
capability scope first.

Dispatch split, deliberate (2026-08-07 tightening pass): Invoice and
Payment-Followup's `issue_invoice`/`send_reminder`/`escalate_to_human` now
call out through a real `notifier` (notifier.py) — a message genuinely
leaves the system, not just a Firestore status flag labeled "sent".
Outreach-Check's `ping_human` stays Firestore-only, unchanged — its whole
point is that first contact is gated to a human noticing and acting
deliberately, not even an automated nudge.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fleet_hackathon.config import (
    COLLECTION_ANALYTICS_FLAGS,
    COLLECTION_CASH_EVENTS,
    COLLECTION_DEALS,
    COLLECTION_INVOICES,
    COLLECTION_LEADS,
)
from fleet_hackathon.notifier import NullNotifier

INVOICE_DUE_WINDOW_DAYS = 14


def issue_invoice(db, deal_id: str, notifier=None) -> dict:
    notifier = notifier or NullNotifier()
    deal_snap = db.collection(COLLECTION_DEALS).document(deal_id).get()
    if not deal_snap.exists:
        raise ValueError(f"deal {deal_id} not found")
    deal = deal_snap.to_dict()

    invoice_id = f"inv-{deal_id}"
    invoice_ref = db.collection(COLLECTION_INVOICES).document(invoice_id)
    if invoice_ref.get().exists:
        return {"invoice_id": invoice_id, "status": "already_issued", "idempotent": True}

    now = datetime.now(UTC)
    invoice_ref.set(
        {
            "invoice_id": invoice_id,
            "deal_id": deal_id,
            "account": deal["account"],
            "amount": deal["amount"],
            "issued_ts": now.isoformat(),
            "due_ts": (now + timedelta(days=INVOICE_DUE_WINDOW_DAYS)).isoformat(),
            "reminders_sent": 0,
            "status": "issued",
        }
    )
    dispatch = notifier.send(
        subject=f"Invoice {invoice_id} issued",
        body=f"Invoice {invoice_id} issued to {deal['account']} for {deal['amount']} (deal {deal_id}).",
    )
    return {"invoice_id": invoice_id, "status": "issued", "idempotent": False, "dispatch": dispatch}


def send_reminder(db, invoice_id: str, tone: str, message: str, notifier=None) -> dict:
    notifier = notifier or NullNotifier()
    invoice_ref = db.collection(COLLECTION_INVOICES).document(invoice_id)
    snap = invoice_ref.get()
    if not snap.exists:
        raise ValueError(f"invoice {invoice_id} not found")
    invoice = snap.to_dict()

    reminders_sent = invoice.get("reminders_sent", 0) + 1
    invoice_ref.set(
        {
            "reminders_sent": reminders_sent,
            "last_reminder_ts": datetime.now(UTC).isoformat(),
            "last_reminder_tone": tone,
            "last_reminder_message": message,
        },
        merge=True,
    )
    dispatch = notifier.send(subject=f"Payment reminder: invoice {invoice_id} ({tone})", body=message)
    return {
        "invoice_id": invoice_id,
        "status": "reminder_sent",
        "reminders_sent": reminders_sent,
        "dispatch": dispatch,
    }


def escalate_to_human(db, invoice_id: str, reason: str, notifier=None) -> dict:
    notifier = notifier or NullNotifier()
    invoice_ref = db.collection(COLLECTION_INVOICES).document(invoice_id)
    if not invoice_ref.get().exists:
        raise ValueError(f"invoice {invoice_id} not found")
    invoice_ref.set(
        {
            "status": "escalated",
            "escalation_reason": reason,
            "escalated_ts": datetime.now(UTC).isoformat(),
        },
        merge=True,
    )
    dispatch = notifier.send(
        subject=f"ACTION NEEDED: invoice {invoice_id} escalated",
        body=f"Invoice {invoice_id} escalated to a human: {reason}",
    )
    return {"invoice_id": invoice_id, "status": "escalated", "dispatch": dispatch}


def ping_human(db, lead_id: str, reason: str) -> dict:
    lead_ref = db.collection(COLLECTION_LEADS).document(lead_id)
    if not lead_ref.get().exists:
        raise ValueError(f"lead {lead_id} not found")
    lead_ref.set(
        {
            "status": "escalated_to_human",
            "escalation_reason": reason,
            "escalated_ts": datetime.now(UTC).isoformat(),
        },
        merge=True,
    )
    return {"lead_id": lead_id, "status": "escalated_to_human"}


def assign_account_manager(db, invoice_id: str, notifier=None) -> dict:
    """Account-Management's one action — hands a paid client's relationship to
    their account manager exactly once. Idempotent: a second call is a no-op."""
    notifier = notifier or NullNotifier()
    invoice_ref = db.collection(COLLECTION_INVOICES).document(invoice_id)
    snap = invoice_ref.get()
    if not snap.exists:
        raise ValueError(f"invoice {invoice_id} not found")
    invoice = snap.to_dict()

    if invoice.get("handed_off"):
        return {"invoice_id": invoice_id, "status": "already_handed_off", "idempotent": True}

    account_manager = invoice.get("account_manager", "Unassigned")
    invoice_ref.set(
        {"handed_off": True, "handed_off_ts": datetime.now(UTC).isoformat(), "handed_off_to": account_manager},
        merge=True,
    )
    dispatch = notifier.send(
        subject=f"New paid account for you: {invoice.get('account', invoice_id)}",
        body=(
            f"{invoice.get('account', invoice_id)} just paid invoice {invoice_id}. "
            f"Handing this relationship to you, {account_manager}, going forward."
        ),
    )
    return {
        "invoice_id": invoice_id,
        "status": "handed_off",
        "handed_off_to": account_manager,
        "idempotent": False,
        "dispatch": dispatch,
    }


def record_payment(db, invoice_id: str) -> dict:
    """Simulates an external event (a payment landing) — not an agent action,
    so it never goes through the Gateway. Same category as seed_demo_data.py:
    injecting state a real payment processor webhook would inject in
    production. Written to the same COLLECTION_CASH_EVENTS ledger shape the
    seeded CRM-sample payments use, so the dashboard's cash-movement feed and
    Payment-Followup's own next cycle both see it identically either way."""
    invoice_ref = db.collection(COLLECTION_INVOICES).document(invoice_id)
    snap = invoice_ref.get()
    if not snap.exists:
        raise ValueError(f"invoice {invoice_id} not found")
    invoice = snap.to_dict()

    if invoice.get("status") == "paid":
        return {"invoice_id": invoice_id, "status": "already_paid", "idempotent": True}

    now = datetime.now(UTC)
    amount = invoice.get("amount", 0)
    invoice_ref.set(
        {"status": "paid", "paid_ts": now.isoformat(), "amount_paid": amount},
        merge=True,
    )
    event_id = f"cash-{uuid.uuid4().hex[:10]}"
    db.collection(COLLECTION_CASH_EVENTS).document(event_id).set(
        {
            "event_id": event_id,
            "direction": "incoming",
            "amount": amount,
            "currency": invoice.get("currency", "USD"),
            "date": now.isoformat(),
            "reference": invoice_id,
            "counterparty": invoice.get("account", invoice_id),
        }
    )
    return {"invoice_id": invoice_id, "status": "paid", "amount": amount, "idempotent": False}


def record_flag(db, dimension: str, severity: str, headline: str, detail: str, metric: dict | None = None) -> dict:
    """Analytics Agent's one action — upserts one flag document per dimension
    (not an ever-growing log, so the dashboard always shows the latest read)
    while the Gateway's audit log still records every cycle's judgment
    separately. Purely a Firestore write of an observation: this never
    touches a client, invoice, or engagement record, and it's the only
    action the Analytics Agent's capability scope permits."""
    db.collection(COLLECTION_ANALYTICS_FLAGS).document(dimension).set(
        {
            "dimension": dimension,
            "severity": severity,
            "headline": headline,
            "detail": detail,
            "metric": metric or {},
            "updated_ts": datetime.now(UTC).isoformat(),
        }
    )
    return {"dimension": dimension, "severity": severity, "headline": headline, "status": "flagged"}
