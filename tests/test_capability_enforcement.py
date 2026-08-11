"""An agent literally cannot call a function outside its declared capability
scope — tested directly, per the build plan's test-coverage requirement."""

import pytest

from fleet_hackathon.capability import (
    ANALYTICS_SCOPE,
    OUTREACH_CHECK_SCOPE,
    PAYMENT_FOLLOWUP_SCOPE,
)
from fleet_hackathon.gateway import CapabilityViolation, Gateway
from fleet_hackathon.registry import AgentRegistry
from fleet_hackathon.telemetry import AuditLogger


def _gateway(fake_db):
    registry = AgentRegistry(fake_db)
    registry.declare(OUTREACH_CHECK_SCOPE)
    registry.declare(PAYMENT_FOLLOWUP_SCOPE)
    audit_logger = AuditLogger(fake_db)
    return Gateway(fake_db, registry, audit_logger), audit_logger


def test_agent_with_no_declared_scope_gets_no_client(fake_db):
    registry = AgentRegistry(fake_db)
    gateway = Gateway(fake_db, registry, AuditLogger(fake_db))
    with pytest.raises(CapabilityViolation):
        gateway.client_for("nonexistent_agent")


def test_outreach_check_cannot_send_reminder(fake_db):
    gateway, _ = _gateway(fake_db)
    client = gateway.client_for("outreach_check")
    with pytest.raises(CapabilityViolation):
        client.call("send_reminder", trace_id="t1", invoice_id="inv-1", tone="gentle", message="hi")


def test_outreach_check_has_no_email_send_capability_at_all(fake_db):
    """Architectural fact, not policy: 'ping_human' is Outreach-Check's only
    action — there is no message-send action anywhere in its declared scope."""
    assert "send_email" not in OUTREACH_CHECK_SCOPE.allowed_actions
    assert "send_message" not in OUTREACH_CHECK_SCOPE.allowed_actions
    assert OUTREACH_CHECK_SCOPE.allowed_actions == frozenset({"ping_human"})


def test_payment_followup_cannot_issue_invoice(fake_db):
    gateway, _ = _gateway(fake_db)
    client = gateway.client_for("payment_followup")
    with pytest.raises(CapabilityViolation):
        client.call("issue_invoice", trace_id="t1", deal_id="deal-1")


def test_blocked_call_is_logged_to_audit(fake_db):
    gateway, audit_logger = _gateway(fake_db)
    client = gateway.client_for("outreach_check")
    with pytest.raises(CapabilityViolation):
        client.call("send_reminder", trace_id="t1", invoice_id="inv-1", tone="gentle", message="hi")

    entries = audit_logger.list_recent()
    assert any(e["status"] == "blocked" and e["agent_name"] == "outreach_check" for e in entries)


def test_disabled_agent_gets_no_client(fake_db):
    """The human-flipped governance switch — the Gateway refuses to issue a
    client at all for a disabled agent, before any scope check happens."""
    registry = AgentRegistry(fake_db)
    registry.declare(OUTREACH_CHECK_SCOPE)
    registry.set_enabled("outreach_check", False)
    gateway = Gateway(fake_db, registry, AuditLogger(fake_db))

    with pytest.raises(CapabilityViolation):
        gateway.client_for("outreach_check")


def test_analytics_cannot_assign_account_manager(fake_db):
    """The Analytics Agent's only capability is `record_flag` — it can
    observe, never act on a client/invoice/engagement record."""
    registry = AgentRegistry(fake_db)
    registry.declare(ANALYTICS_SCOPE)
    gateway = Gateway(fake_db, registry, AuditLogger(fake_db))
    client = gateway.client_for("analytics")
    with pytest.raises(CapabilityViolation):
        client.call("assign_account_manager", trace_id="t1", invoice_id="inv-1")


def test_analytics_scope_is_record_flag_only():
    assert ANALYTICS_SCOPE.allowed_actions == frozenset({"record_flag"})


def test_re_declaring_a_scope_preserves_a_human_disable_toggle(fake_db):
    """ensure_registered() runs on every cold start — it must never silently
    re-enable an agent a human turned off."""
    registry = AgentRegistry(fake_db)
    registry.declare(OUTREACH_CHECK_SCOPE)
    registry.set_enabled("outreach_check", False)

    registry.declare(OUTREACH_CHECK_SCOPE)  # simulates a redeploy/cold-start re-declaration

    assert registry.get("outreach_check").enabled is False


def test_allowed_action_within_scope_succeeds(fake_db):
    from fleet_hackathon.capability import OUTREACH_CHECK_SCOPE as scope
    from fleet_hackathon.config import COLLECTION_LEADS

    fake_db.collection(COLLECTION_LEADS).document("lead-1").set({"lead_id": "lead-1", "status": "new"})
    gateway, audit_logger = _gateway(fake_db)
    client = gateway.client_for("outreach_check")

    result = client.call("ping_human", trace_id="t1", lead_id="lead-1", reason="past SLA")
    assert result["status"] == "escalated_to_human"

    entries = audit_logger.list_recent()
    assert any(e["status"] == "escalated_to_human" and e["success_criterion"] == scope.success_criterion for e in entries)
