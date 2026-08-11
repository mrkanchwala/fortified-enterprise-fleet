"""Account-Management's decision logic — pure, deterministic, the natural
next step after an invoice is actually paid AND delivered (2026-08-10:
delivery-status gate added, fail-closed on missing/incomplete delivery)."""

from fleet_hackathon.agents.account_management import decide


def test_unpaid_invoice_takes_no_action():
    decision = decide({"invoice_id": "inv-1", "status": "issued"})
    assert decision.should_handoff is False


def test_paid_and_delivered_invoice_triggers_handoff():
    decision = decide({"invoice_id": "inv-1", "status": "paid"}, deal={"delivery_status": "Delivered"})
    assert decision.should_handoff is True


def test_already_handed_off_invoice_does_not_repeat():
    decision = decide({"invoice_id": "inv-1", "status": "paid", "handed_off": True})
    assert decision.should_handoff is False


def test_paid_invoice_with_incomplete_delivery_is_blocked():
    decision = decide({"invoice_id": "inv-1", "status": "paid"}, deal={"delivery_status": "Delayed"})
    assert decision.should_handoff is False
    assert "Delayed" in decision.reason


def test_paid_invoice_with_no_linked_deal_is_blocked_fail_closed():
    """No deal record found at all — fail-closed, not fail-open."""
    decision = decide({"invoice_id": "inv-1", "status": "paid"}, deal=None)
    assert decision.should_handoff is False
    assert "no delivery record" in decision.reason.lower()


def test_paid_invoice_with_deal_missing_delivery_status_is_blocked_fail_closed():
    """Deal record exists but never got a delivery_status field — still fail-closed."""
    decision = decide({"invoice_id": "inv-1", "status": "paid"}, deal={"account": "Some Client"})
    assert decision.should_handoff is False
    assert "no delivery record" in decision.reason.lower()
