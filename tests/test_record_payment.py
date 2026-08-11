"""actions.record_payment — simulates a payment landing (what /mark-paid
calls). Not an agent action: no Gateway, no capability scope, same category
as seed_demo_data.py injecting external state."""

import pytest

from fleet_hackathon import actions
from fleet_hackathon.config import COLLECTION_CASH_EVENTS, COLLECTION_INVOICES


def test_record_payment_marks_invoice_paid(fake_db):
    fake_db.collection(COLLECTION_INVOICES).document("inv-1").set(
        {"invoice_id": "inv-1", "amount": 500, "currency": "USD", "account": "Acme Co", "status": "issued"}
    )
    result = actions.record_payment(fake_db, "inv-1")

    assert result["status"] == "paid"
    invoice = fake_db.collection(COLLECTION_INVOICES).document("inv-1").get().to_dict()
    assert invoice["status"] == "paid"
    assert invoice["amount_paid"] == 500


def test_record_payment_writes_a_cash_event(fake_db):
    fake_db.collection(COLLECTION_INVOICES).document("inv-1").set(
        {"invoice_id": "inv-1", "amount": 500, "currency": "USD", "account": "Acme Co", "status": "issued"}
    )
    actions.record_payment(fake_db, "inv-1")

    events = [doc.to_dict() for doc in fake_db.collection(COLLECTION_CASH_EVENTS).stream()]
    assert len(events) == 1
    assert events[0]["direction"] == "incoming"
    assert events[0]["amount"] == 500
    assert events[0]["reference"] == "inv-1"


def test_record_payment_is_idempotent(fake_db):
    fake_db.collection(COLLECTION_INVOICES).document("inv-1").set(
        {"invoice_id": "inv-1", "amount": 500, "currency": "USD", "account": "Acme Co", "status": "issued"}
    )
    actions.record_payment(fake_db, "inv-1")
    second = actions.record_payment(fake_db, "inv-1")

    assert second["idempotent"] is True
    events = [doc.to_dict() for doc in fake_db.collection(COLLECTION_CASH_EVENTS).stream()]
    assert len(events) == 1, "a second call must not write a duplicate cash event"


def test_record_payment_unknown_invoice_raises(fake_db):
    with pytest.raises(ValueError):
        actions.record_payment(fake_db, "does-not-exist")
