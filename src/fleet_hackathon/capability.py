"""Agent Identity — the declared capability scope an agent is constructed with.

A CapabilityScope is not an instruction the agent could ignore; it is the input the
Gateway uses to build the only handle the agent ever receives (see gateway.py).

`enabled` is a human-flipped governance switch (see runtime.set_agent_enabled) —
a CFO/COO can turn an agent off if it's not needed, or back on if a gap needs
covering. The system never flips this itself; it only ever surfaces the signal
(see dashboard.py's gap metrics) that might prompt a human to.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityScope:
    agent_name: str
    allowed_actions: frozenset[str]
    success_criterion: str
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "allowed_actions": sorted(self.allowed_actions),
            "success_criterion": self.success_criterion,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: dict) -> "CapabilityScope":
        return CapabilityScope(
            agent_name=data["agent_name"],
            allowed_actions=frozenset(data["allowed_actions"]),
            success_criterion=data["success_criterion"],
            enabled=data.get("enabled", True),
        )


# The 4 agents' declared scopes — single source of truth, imported by both the
# registry (to persist the declaration) and the agents themselves (to request a
# gateway client). Outreach-Check has no email/message-send action at all: that's
# an architectural fact, not a policy an instruction could override.
OUTREACH_CHECK_SCOPE = CapabilityScope(
    agent_name="outreach_check",
    allowed_actions=frozenset({"ping_human"}),
    success_criterion=(
        "Never contacts a lead directly. If a lead hasn't been followed up with in "
        "time, it notifies a team member instead of reaching out itself."
    ),
)

INVOICE_SCOPE = CapabilityScope(
    agent_name="invoice",
    allowed_actions=frozenset({"issue_invoice"}),
    success_criterion=(
        "Sends exactly one invoice per won deal — never sends the same invoice "
        "twice, even if it runs again."
    ),
)

PAYMENT_FOLLOWUP_SCOPE = CapabilityScope(
    agent_name="payment_followup",
    allowed_actions=frozenset({"send_reminder", "escalate_to_human"}),
    success_criterion=(
        "Sends at most one reminder at a time. Once an invoice is 30+ days "
        "overdue or has already gotten 2 reminders, it hands off to a team "
        "member instead of sending another reminder."
    ),
)

ACCOUNT_MANAGEMENT_SCOPE = CapabilityScope(
    agent_name="account_management",
    allowed_actions=frozenset({"assign_account_manager"}),
    success_criterion=(
        "Once an invoice is paid AND the linked engagement's delivery is complete, hands "
        "the client relationship to their account manager exactly once — never repeats "
        "the handoff, and never hands off a client whose delivery isn't finished or whose "
        "delivery status is unknown."
    ),
)

ANALYTICS_SCOPE = CapabilityScope(
    agent_name="analytics",
    allowed_actions=frozenset({"record_flag"}),
    success_criterion=(
        "Only ever writes an observation for a human to review — across cash movements, "
        "operational bottlenecks, growth, sales pipeline, supplier relationships, and "
        "payables backlog. Never touches a client, invoice, or engagement record, and "
        "never changes which agents are enabled — it flags, a human decides."
    ),
)

ALL_SCOPES = (
    OUTREACH_CHECK_SCOPE,
    INVOICE_SCOPE,
    PAYMENT_FOLLOWUP_SCOPE,
    ACCOUNT_MANAGEMENT_SCOPE,
    ANALYTICS_SCOPE,
)
