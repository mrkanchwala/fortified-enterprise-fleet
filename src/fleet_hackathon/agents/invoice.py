"""Invoice Agent — issues an invoice on a (simulated) deal-closed signal.

Fully autonomous: no human-escalation path in its declared scope at all
(capability.py's INVOICE_SCOPE) — issuing an invoice for a genuinely
closed-won deal is judged reversible/low-stakes enough not to need one.
"""

from dataclasses import dataclass

from fleet_hackathon.capability import INVOICE_SCOPE
from fleet_hackathon.config import COLLECTION_DEALS, COLLECTION_INVOICES


@dataclass
class InvoiceDecision:
    deal_id: str
    should_issue: bool
    reason: str


def decide(deal: dict, invoice_already_exists: bool) -> InvoiceDecision:
    """Pure and deterministic — the unit-testable core, no LLM/network call."""
    deal_id = deal["deal_id"]
    if deal.get("status") != "closed_won":
        return InvoiceDecision(deal_id, False, f"deal status is '{deal.get('status')}', not closed_won yet")
    if invoice_already_exists:
        return InvoiceDecision(deal_id, False, "invoice already issued for this deal (idempotent no-op)")
    return InvoiceDecision(deal_id, True, "deal is closed_won and no invoice exists yet")


def run_cycle(db, gateway, audit_logger) -> list[dict]:
    client = gateway.client_for("invoice")
    results = []

    for doc in db.collection(COLLECTION_DEALS).stream():
        deal = doc.to_dict()
        trace_id = audit_logger.new_trace_id()

        invoice_id = f"inv-{deal['deal_id']}"
        invoice_exists = db.collection(COLLECTION_INVOICES).document(invoice_id).get().exists

        decision = decide(deal, invoice_exists)
        if decision.should_issue:
            client.call("issue_invoice", trace_id=trace_id, deal_id=decision.deal_id)
        else:
            audit_logger.log(
                trace_id=trace_id,
                agent_name="invoice",
                action="no_action",
                status="ok",
                detail=decision.reason,
                success_criterion=INVOICE_SCOPE.success_criterion,
                attributes={"deal_id": decision.deal_id},
            )

        results.append({"deal_id": decision.deal_id, "issued": decision.should_issue, "reason": decision.reason})
    return results
