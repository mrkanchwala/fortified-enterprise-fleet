"""Analytics Agent — the 6 dimension decision functions are pure and
deterministic (no LLM/network call), and run_cycle wires them through the
same Gateway/Registry/Audit-log path as the other 4 agents."""

from datetime import UTC, datetime, timedelta

from fleet_hackathon.agents import analytics as analytics_agent
from fleet_hackathon.capability import ANALYTICS_SCOPE
from fleet_hackathon.config import (
    COLLECTION_ANALYTICS_FLAGS,
    COLLECTION_BILLS,
    COLLECTION_CASH_EVENTS,
    COLLECTION_GROWTH_SNAPSHOT,
    COLLECTION_PORTFOLIO_ENGAGEMENTS,
    COLLECTION_PORTFOLIO_INVOICES,
    COLLECTION_SALES_PIPELINE,
)
from fleet_hackathon.gateway import Gateway
from fleet_hackathon.registry import AgentRegistry
from fleet_hackathon.telemetry import AuditLogger

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------- decide_cash_movements


def test_cash_movements_ok_when_net_positive_and_dso_in_range():
    cash_events = [{"direction": "incoming", "amount": 10000, "date": _iso(NOW - timedelta(days=5))}]
    invoices = [{"amount": 1000, "amount_paid": 1000, "status": "paid"}]
    decision = analytics_agent.decide_cash_movements(cash_events, invoices, NOW)
    assert decision.severity == "ok"


def test_cash_movements_attention_when_net_negative_and_dso_over_benchmark():
    cash_events = [{"direction": "outgoing", "amount": 5000, "date": _iso(NOW - timedelta(days=2))}]
    # Fully outstanding, unpaid invoice -> DSO = outstanding/invoiced * 90 = 90 days, over the 52-day benchmark.
    invoices = [{"amount": 20000, "amount_paid": 0, "status": "issued"}]
    decision = analytics_agent.decide_cash_movements(cash_events, invoices, NOW)
    assert decision.severity == "attention"
    assert decision.metric["net_30d"] == -5000


# ---------------------------------------------------------------- decide_operational_bottlenecks


def test_operational_bottlenecks_ok_when_all_delivered():
    engagements = [
        {"status": "Delivered", "due_date": _iso(NOW - timedelta(days=10)), "delivered_date": _iso(NOW - timedelta(days=12))}
        for _ in range(10)
    ]
    decision = analytics_agent.decide_operational_bottlenecks(engagements, NOW)
    assert decision.severity == "ok"


def test_operational_bottlenecks_attention_when_a_fifth_are_stuck():
    on_track = [
        {"status": "Delivered", "due_date": _iso(NOW - timedelta(days=1)), "delivered_date": _iso(NOW - timedelta(days=2))}
        for _ in range(8)
    ]
    stuck = [{"status": "Delayed", "due_date": _iso(NOW - timedelta(days=5)), "delivered_date": None} for _ in range(2)]
    decision = analytics_agent.decide_operational_bottlenecks(on_track + stuck, NOW)
    assert decision.severity == "attention"
    assert decision.metric["stuck_count"] == 2


def test_operational_bottlenecks_counts_overdue_undelivered_even_without_delayed_status():
    engagements = [{"status": "In Progress", "due_date": _iso(NOW - timedelta(days=3)), "delivered_date": None}]
    decision = analytics_agent.decide_operational_bottlenecks(engagements, NOW)
    assert decision.metric["stuck_count"] == 1


def test_operational_bottlenecks_no_data_yet():
    decision = analytics_agent.decide_operational_bottlenecks([], NOW)
    assert decision.severity == "ok"
    assert decision.metric["total"] == 0


def test_operational_bottlenecks_detail_names_the_stuck_clients():
    """2026-08-10 entity-naming: the detail must name which client(s) are
    actually stuck, not just state a count."""
    engagements = [
        {"client": "Acme Corp", "status": "Delayed", "due_date": _iso(NOW - timedelta(days=5)), "delivered_date": None},
        {"client": "Beta Inc", "status": "On Hold", "due_date": _iso(NOW - timedelta(days=5)), "delivered_date": None},
    ]
    decision = analytics_agent.decide_operational_bottlenecks(engagements, NOW)
    assert "Acme Corp" in decision.detail
    assert "Beta Inc" in decision.detail
    assert decision.metric["stuck_clients"] == ["Acme Corp", "Beta Inc"]


def test_operational_bottlenecks_detail_truncates_beyond_the_cap():
    engagements = [
        {"client": f"Client {i}", "status": "Delayed", "due_date": _iso(NOW - timedelta(days=5)), "delivered_date": None}
        for i in range(5)
    ]
    decision = analytics_agent.decide_operational_bottlenecks(engagements, NOW)
    assert "+2 more" in decision.detail
    assert "Client 4" not in decision.detail  # beyond the cap of 3


# ---------------------------------------------------------------- decide_growth


def test_growth_ok_when_up_meaningfully():
    decision = analytics_agent.decide_growth({"current_period_value": 120, "prior_period_value": 100})
    assert decision.severity == "ok"
    assert decision.metric["growth_pct"] == 20.0


def test_growth_watch_when_nearly_flat():
    decision = analytics_agent.decide_growth({"current_period_value": 102, "prior_period_value": 100})
    assert decision.severity == "watch"


def test_growth_attention_when_down():
    decision = analytics_agent.decide_growth({"current_period_value": 80, "prior_period_value": 100})
    assert decision.severity == "attention"


def test_growth_ok_with_no_prior_period_data():
    decision = analytics_agent.decide_growth({})
    assert decision.severity == "ok"


# ---------------------------------------------------------------- decide_sales_pipeline


def test_sales_pipeline_ok_healthy():
    pipeline = {
        "won_count": 90,
        "lost_count": 10,
        "proposal_count": 20,
        "negotiation_count": 10,
        "proposal_value": 50000,
        "negotiation_value": 30000,
    }
    decision = analytics_agent.decide_sales_pipeline(pipeline)
    assert decision.severity == "ok"
    assert decision.metric["win_rate_pct"] == 90.0


def test_sales_pipeline_attention_when_win_rate_low():
    pipeline = {"won_count": 30, "lost_count": 70, "proposal_count": 20, "negotiation_count": 10}
    decision = analytics_agent.decide_sales_pipeline(pipeline)
    assert decision.severity == "attention"


def test_sales_pipeline_watch_when_open_pipeline_thin():
    pipeline = {"won_count": 90, "lost_count": 10, "proposal_count": 2, "negotiation_count": 1}
    decision = analytics_agent.decide_sales_pipeline(pipeline)
    assert decision.severity == "watch"


# ---------------------------------------------------------------- decide_supplier_relationship


def test_supplier_relationship_ok_when_nothing_overdue():
    bills = [{"status": "paid", "due_ts": _iso(NOW - timedelta(days=5)), "amount": 100}]
    decision = analytics_agent.decide_supplier_relationship(bills, NOW)
    assert decision.severity == "ok"


def test_supplier_relationship_attention_when_three_or_more_overdue():
    bills = [{"status": "issued", "due_ts": _iso(NOW - timedelta(days=1)), "amount": 100} for _ in range(3)]
    decision = analytics_agent.decide_supplier_relationship(bills, NOW)
    assert decision.severity == "attention"
    assert decision.metric["overdue_count"] == 3


def test_supplier_relationship_ignores_bills_not_yet_due():
    bills = [{"status": "issued", "due_ts": _iso(NOW + timedelta(days=10)), "amount": 100}]
    decision = analytics_agent.decide_supplier_relationship(bills, NOW)
    assert decision.severity == "ok"


def test_supplier_relationship_detail_names_the_overdue_suppliers():
    """2026-08-10 entity-naming: the detail must name which supplier(s) are
    actually overdue, not just state a count."""
    bills = [
        {"supplier": "Acme Supplies", "status": "issued", "due_ts": _iso(NOW - timedelta(days=1)), "amount": 100},
        {"supplier": "Beta Logistics", "status": "issued", "due_ts": _iso(NOW - timedelta(days=1)), "amount": 200},
    ]
    decision = analytics_agent.decide_supplier_relationship(bills, NOW)
    assert "Acme Supplies" in decision.detail
    assert "Beta Logistics" in decision.detail
    assert decision.metric["overdue_suppliers"] == ["Acme Supplies", "Beta Logistics"]


# ---------------------------------------------------------------- decide_payables_backlog


def test_payables_backlog_ok_when_nothing_unpaid():
    decision = analytics_agent.decide_payables_backlog([{"status": "paid", "amount": 500}])
    assert decision.severity == "ok"


def test_payables_backlog_attention_over_threshold():
    bills = [{"status": "issued", "amount": 25000}]
    decision = analytics_agent.decide_payables_backlog(bills)
    assert decision.severity == "attention"


def test_payables_backlog_watch_below_threshold():
    bills = [{"status": "issued", "amount": 500}]
    decision = analytics_agent.decide_payables_backlog(bills)
    assert decision.severity == "watch"


def test_payables_backlog_detail_names_the_unpaid_suppliers():
    """2026-08-10 entity-naming: the detail must name which supplier(s) the
    unpaid value sits with, not just state a total."""
    bills = [{"supplier": "Acme Supplies", "status": "issued", "amount": 500}]
    decision = analytics_agent.decide_payables_backlog(bills)
    assert "Acme Supplies" in decision.detail
    assert decision.metric["unpaid_suppliers"] == ["Acme Supplies"]


# ---------------------------------------------------------------- run_cycle (integration)


def _gateway_with_analytics(fake_db):
    registry = AgentRegistry(fake_db)
    registry.declare(ANALYTICS_SCOPE)
    audit_logger = AuditLogger(fake_db)
    return Gateway(fake_db, registry, audit_logger), audit_logger


def test_run_cycle_writes_one_flag_per_dimension(fake_db):
    fake_db.collection(COLLECTION_PORTFOLIO_INVOICES).document("pf-1").set(
        {"amount": 1000, "amount_paid": 1000, "status": "paid"}
    )
    fake_db.collection(COLLECTION_BILLS).document("bill-1").set({"status": "paid", "amount": 100})
    fake_db.collection(COLLECTION_PORTFOLIO_ENGAGEMENTS).document("eng-1").set(
        {"status": "Delivered", "due_date": None, "delivered_date": None}
    )
    fake_db.collection(COLLECTION_GROWTH_SNAPSHOT).document("current").set(
        {"current_period_value": 120, "prior_period_value": 100, "current_period_deal_count": 5, "prior_period_deal_count": 4}
    )
    fake_db.collection(COLLECTION_SALES_PIPELINE).document("current").set(
        {"won_count": 9, "lost_count": 1, "proposal_count": 10, "negotiation_count": 5}
    )

    gateway, audit_logger = _gateway_with_analytics(fake_db)
    results = analytics_agent.run_cycle(fake_db, gateway, audit_logger)

    assert len(results) == 6
    flags = {doc.id: doc.to_dict() for doc in fake_db.collection(COLLECTION_ANALYTICS_FLAGS).stream()}
    assert set(flags.keys()) == {
        "cash_movements",
        "operational_bottlenecks",
        "growth",
        "sales_pipeline",
        "supplier_relationship",
        "payables_backlog",
    }
    entries = audit_logger.list_recent()
    assert all(e["agent_name"] == "analytics" for e in entries)
    assert all(e["action"] == "record_flag" for e in entries)


def test_run_cycle_is_idempotent_per_dimension(fake_db):
    """A 2nd cycle overwrites the same 6 docs rather than accumulating more —
    the dashboard should always show the latest read, not a growing list."""
    gateway, audit_logger = _gateway_with_analytics(fake_db)
    analytics_agent.run_cycle(fake_db, gateway, audit_logger)
    analytics_agent.run_cycle(fake_db, gateway, audit_logger)

    flags = list(fake_db.collection(COLLECTION_ANALYTICS_FLAGS).stream())
    assert len(flags) == 6


def test_cash_events_collection_stays_untouched_by_a_run(fake_db):
    """The Analytics Agent only ever reads cash events — it never writes to
    that collection (record_payment is the only writer, and that's not an
    agent action at all)."""
    gateway, audit_logger = _gateway_with_analytics(fake_db)
    analytics_agent.run_cycle(fake_db, gateway, audit_logger)
    assert list(fake_db.collection(COLLECTION_CASH_EVENTS).stream()) == []
