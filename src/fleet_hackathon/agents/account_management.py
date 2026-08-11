"""Account-Management Agent — closes the loop once an invoice is paid by
handing the client relationship to their account manager. The natural next
step after Payment-Followup's job is done, not a bolted-on extra: it only
ever fires on a real payment event (real or the demo's simulated one),
never on its own initiative.

Delivery-status gate (2026-08-10): payment alone isn't sufficient — handing a
client to their account manager before the work is actually delivered would
be premature. `decide()` also checks the linked deal's `delivery_status`, and
is fail-closed: a missing deal record or a missing/non-"Delivered" status
blocks the handoff rather than silently proceeding. A blocked-with-a-stated-
reason state is safer to demo (and safer in general) than a silent skip.
"""

from dataclasses import dataclass

from fleet_hackathon.capability import ACCOUNT_MANAGEMENT_SCOPE
from fleet_hackathon.config import COLLECTION_DEALS, COLLECTION_INVOICES

DELIVERED_STATUS = "Delivered"


@dataclass
class HandoffDecision:
    invoice_id: str
    should_handoff: bool
    reason: str


def decide(invoice: dict, deal: dict | None = None) -> HandoffDecision:
    """Pure and deterministic — the unit-testable core, no LLM/network call.
    `deal` is the linked COLLECTION_DEALS record (or None if not found)."""
    invoice_id = invoice["invoice_id"]
    if invoice.get("status") != "paid":
        return HandoffDecision(invoice_id, False, "Invoice not paid yet — nothing to hand off")
    if invoice.get("handed_off"):
        return HandoffDecision(invoice_id, False, "Already handed off — no repeat action")

    delivery_status = deal.get("delivery_status") if deal else None
    if delivery_status is None:
        return HandoffDecision(
            invoice_id, False, "No delivery record found for this invoice — handoff blocked until delivery status is known"
        )
    if delivery_status != DELIVERED_STATUS:
        return HandoffDecision(
            invoice_id, False, f"Delivery status is '{delivery_status}' — handoff blocked until delivery is complete"
        )

    return HandoffDecision(invoice_id, True, "Invoice paid and delivered — handing the relationship to the account manager")


def run_cycle(db, gateway, audit_logger) -> list[dict]:
    client = gateway.client_for("account_management")
    results = []

    for doc in db.collection(COLLECTION_INVOICES).stream():
        invoice = doc.to_dict()
        invoice_id = invoice["invoice_id"]
        trace_id = audit_logger.new_trace_id()

        deal_snap = db.collection(COLLECTION_DEALS).document(invoice.get("deal_id", "")).get()
        deal = deal_snap.to_dict() if deal_snap.exists else None

        decision = decide(invoice, deal)
        if decision.should_handoff:
            client.call("assign_account_manager", trace_id=trace_id, invoice_id=invoice_id)
        else:
            audit_logger.log(
                trace_id=trace_id,
                agent_name="account_management",
                action="no_action",
                status="ok",
                detail=decision.reason,
                success_criterion=ACCOUNT_MANAGEMENT_SCOPE.success_criterion,
                attributes={"invoice_id": invoice_id},
            )

        results.append({"invoice_id": invoice_id, "handed_off": decision.should_handoff, "reason": decision.reason})
    return results
