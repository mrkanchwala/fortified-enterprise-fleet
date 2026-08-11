"""Analytics Agent — reads across the book of business and flags what
management should look at, before anyone has to go looking. This is the
Fleet track's Telemetry & Observability component made into a real 5th
agent (not a passive dashboard computation): its judgment goes through the
same Gateway/Registry/Audit-log path as the other 4, so it's visible,
capability-scoped, and disableable by a human like every other agent here.
It never touches the fleet's own configuration — it only ever writes an
observation via `record_flag`, the one action its capability scope permits.

Six dimensions (2026-08-07, direct instruction), each backed by real data
sourced from Quadriga's own `crm_demo.db` (same "real samples, remapped
dates" discipline as seed_demo_data.py, cited inline where seeded):
- Cash movements — trailing 30-day in/out (COLLECTION_CASH_EVENTS) + DSO
  vs. the same 2026 benchmark the Executive Snapshot already cites.
- Operational bottlenecks — engagements Delayed/On Hold, or past their due
  date with nothing delivered (COLLECTION_PORTFOLIO_ENGAGEMENTS).
- Growth % — trailing quarter vs. prior quarter Won-contract value
  (COLLECTION_GROWTH_SNAPSHOT, a static real snapshot, not live-recomputed
  every cycle — same treatment as the DSO/AR-turnover benchmarks already
  cited elsewhere in this codebase).
- Sales pipeline — win rate and open-pipeline size (COLLECTION_SALES_PIPELINE,
  same static-snapshot treatment).
- Supplier relationship — bills overdue past their due date, a vendor-payment
  risk signal (COLLECTION_BILLS).
- Payables backlog — the honest substitute for "inventory levels": this CRM
  models a B2B professional-services business with no inventory/SKU table at
  all, so rather than fabricate a stock count, this dimension reads the same
  COLLECTION_BILLS data as a working-capital-lockup signal (total value and
  count of bills not yet cleared) — a real COO-relevant number, just not a
  literal inventory count.

Entity-naming (2026-08-10): a flag should point at a real record, not just
an aggregate count, wherever the underlying data actually supports it.
Operational bottlenecks, supplier relationship, and payables backlog each
name the specific client(s)/supplier(s) behind the number (capped, see
NAMED_ENTITY_CAP). Growth and sales pipeline deliberately stay aggregate-only
— COLLECTION_GROWTH_SNAPSHOT/COLLECTION_SALES_PIPELINE are static
quarter-level snapshots with no individual-deal data seeded, so naming a
"specific deal" there would mean fabricating one — the same honesty
discipline as the payables-backlog substitution above.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fleet_hackathon import analytics as biz_analytics
from fleet_hackathon.capability import ANALYTICS_SCOPE
from fleet_hackathon.config import (
    COLLECTION_BILLS,
    COLLECTION_CASH_EVENTS,
    COLLECTION_GROWTH_SNAPSHOT,
    COLLECTION_PORTFOLIO_ENGAGEMENTS,
    COLLECTION_PORTFOLIO_INVOICES,
    COLLECTION_SALES_PIPELINE,
)

BOTTLENECK_ATTENTION_PCT = 0.20
BOTTLENECK_WATCH_PCT = 0.10
GROWTH_WATCH_FLOOR_PCT = 5.0
PIPELINE_WIN_RATE_FLOOR_PCT = 60.0
PIPELINE_THIN_DEAL_COUNT = 5
SUPPLIER_ATTENTION_COUNT = 3
PAYABLES_ATTENTION_VALUE = 20000.0

# How many specific offending records to name inline before truncating
# (2026-08-10, entity-naming) — a flag should point at a real record, not
# just an aggregate count, but an unbounded list would defeat the point of
# a scannable headline/detail. Only the 3 dimensions with real per-record
# data (operational_bottlenecks, supplier_relationship, payables_backlog)
# get this — growth/sales_pipeline are static quarter-level snapshots with
# no individual-deal data seeded, so naming a "specific deal" there would
# mean fabricating one. Documented in each decide_* docstring below.
NAMED_ENTITY_CAP = 3


def _named_entities(names: list[str]) -> str:
    shown = [n for n in names[:NAMED_ENTITY_CAP]]
    extra = len(names) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += f", +{extra} more"
    return text


@dataclass
class FlagDecision:
    dimension: str
    severity: str  # "ok" | "watch" | "attention"
    headline: str
    detail: str
    metric: dict


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _within_trailing_days(ts: str | None, now: datetime, days: int) -> bool:
    if not ts:
        return False
    return 0 <= (now - _parse(ts)).days <= days


def decide_cash_movements(cash_events: list[dict], portfolio_invoices: list[dict], now: datetime) -> FlagDecision:
    """Pure and deterministic — the unit-testable core, no LLM/network call."""
    trailing = [e for e in cash_events if _within_trailing_days(e.get("date"), now, 30)]
    inflow = sum(e.get("amount", 0) for e in trailing if e.get("direction") == "incoming")
    outflow = sum(e.get("amount", 0) for e in trailing if e.get("direction") == "outgoing")
    net = inflow - outflow

    dso_value = biz_analytics.dso(portfolio_invoices, now=now)
    dso_over_benchmark = dso_value is not None and dso_value > biz_analytics.DSO_BENCHMARK_DAYS
    dso_note = (
        f"DSO is {dso_value:.0f} days vs. the {biz_analytics.DSO_BENCHMARK_DAYS:.0f}-day benchmark."
        if dso_value is not None
        else "Not enough paid history yet to compute DSO."
    )
    metric = {
        "net_30d": round(net, 2),
        "inflow_30d": round(inflow, 2),
        "outflow_30d": round(outflow, 2),
        "dso_days": round(dso_value, 1) if dso_value is not None else None,
    }
    base_detail = f"Trailing 30 days: ${inflow:,.0f} in, ${outflow:,.0f} out — net ${net:,.0f}. {dso_note}"

    if net < 0 and dso_over_benchmark:
        return FlagDecision(
            "cash_movements", "attention",
            f"Net cash outflow this month (${abs(net):,.0f}) and DSO above benchmark",
            base_detail, metric,
        )
    if net < 0 or dso_over_benchmark:
        headline = "Net cash outflow this month" if net < 0 else "DSO is above benchmark"
        return FlagDecision("cash_movements", "watch", headline, base_detail, metric)
    return FlagDecision("cash_movements", "ok", "Cash movement looks healthy", base_detail, metric)


def decide_operational_bottlenecks(engagements: list[dict], now: datetime) -> FlagDecision:
    """Entity-naming (2026-08-10): `client` is a real field on every seeded
    engagement, so the detail names which client(s) are actually stuck,
    capped at NAMED_ENTITY_CAP, rather than only stating a count."""
    stuck = [
        e
        for e in engagements
        if e.get("status") in ("Delayed", "On Hold")
        or (
            e.get("status") not in ("Delivered", "Cancelled")
            and e.get("due_date")
            and not e.get("delivered_date")
            and _parse(e["due_date"]) < now
        )
    ]
    total = len(engagements)
    pct = (len(stuck) / total) if total else 0.0
    stuck_clients = [e.get("client", "unknown") for e in stuck]
    metric = {"stuck_count": len(stuck), "total": total, "pct": round(pct * 100, 1), "stuck_clients": stuck_clients}
    names = f" — {_named_entities(stuck_clients)}" if stuck_clients else ""
    detail = f"{len(stuck)} of {total} engagements are Delayed, On Hold, or overdue with nothing delivered{names}."

    if total and pct >= BOTTLENECK_ATTENTION_PCT:
        return FlagDecision(
            "operational_bottlenecks", "attention",
            f"{len(stuck)} of {total} engagements ({pct * 100:.0f}%) are stuck", detail, metric,
        )
    if total and pct >= BOTTLENECK_WATCH_PCT:
        return FlagDecision(
            "operational_bottlenecks", "watch",
            f"{len(stuck)} of {total} engagements ({pct * 100:.0f}%) are stuck", detail, metric,
        )
    return FlagDecision(
        "operational_bottlenecks", "ok", f"Only {len(stuck)} of {total} engagements stuck",
        "Delivery is broadly on track." if total else "No engagement data yet.", metric,
    )


def decide_growth(snapshot: dict) -> FlagDecision:
    current = snapshot.get("current_period_value", 0.0)
    prior = snapshot.get("prior_period_value", 0.0)
    if not prior:
        return FlagDecision("growth", "ok", "Not enough history yet to compute growth", "No prior-period data.", {})

    growth_pct = (current - prior) / prior * 100
    metric = {
        "current_period_value": current,
        "prior_period_value": prior,
        "growth_pct": round(growth_pct, 1),
    }
    detail = (
        f"${current:,.0f} won this quarter vs. ${prior:,.0f} the quarter before "
        f"({snapshot.get('current_period_deal_count', '?')} vs. {snapshot.get('prior_period_deal_count', '?')} deals)."
    )

    if growth_pct < 0:
        return FlagDecision("growth", "attention", f"Won-deal value down {abs(growth_pct):.0f}% vs. prior quarter", detail, metric)
    if growth_pct < GROWTH_WATCH_FLOOR_PCT:
        return FlagDecision("growth", "watch", f"Growth nearly flat ({growth_pct:.0f}%)", detail, metric)
    return FlagDecision("growth", "ok", f"Won-deal value up {growth_pct:.0f}% vs. prior quarter", detail, metric)


def decide_sales_pipeline(pipeline: dict) -> FlagDecision:
    won = pipeline.get("won_count", 0)
    lost = pipeline.get("lost_count", 0)
    proposal = pipeline.get("proposal_count", 0)
    negotiation = pipeline.get("negotiation_count", 0)
    closed = won + lost
    win_rate = (won / closed * 100) if closed else None
    open_count = proposal + negotiation
    open_value = pipeline.get("proposal_value", 0.0) + pipeline.get("negotiation_value", 0.0)

    metric = {
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "open_pipeline_count": open_count,
        "open_pipeline_value": round(open_value, 2),
    }
    win_rate_note = f"Win rate {win_rate:.0f}%." if win_rate is not None else "Not enough closed deals to compute a win rate."
    detail = f"{won} won vs. {lost} lost. {open_count} deals worth ${open_value:,.0f} still open. {win_rate_note}"

    if win_rate is not None and win_rate < PIPELINE_WIN_RATE_FLOOR_PCT:
        return FlagDecision("sales_pipeline", "attention", f"Win rate is {win_rate:.0f}%, below the {PIPELINE_WIN_RATE_FLOOR_PCT:.0f}% floor", detail, metric)
    if open_count < PIPELINE_THIN_DEAL_COUNT:
        return FlagDecision("sales_pipeline", "watch", "Open pipeline looks thin", detail, metric)
    headline = f"Win rate {win_rate:.0f}%, pipeline healthy" if win_rate is not None else "Pipeline healthy"
    return FlagDecision("sales_pipeline", "ok", headline, detail, metric)


def decide_supplier_relationship(bills: list[dict], now: datetime) -> FlagDecision:
    """Entity-naming (2026-08-10): `supplier` is a real field on every seeded
    bill, so the detail names which supplier(s) are actually overdue, capped
    at NAMED_ENTITY_CAP, rather than only stating a count."""
    overdue = [b for b in bills if b.get("status") != "paid" and b.get("due_ts") and _parse(b["due_ts"]) < now]
    overdue_value = sum(b.get("amount", 0) for b in overdue)
    overdue_suppliers = [b.get("supplier", "unknown") for b in overdue]
    metric = {
        "overdue_count": len(overdue),
        "overdue_value": round(overdue_value, 2),
        "overdue_suppliers": overdue_suppliers,
    }
    names = f" — {_named_entities(overdue_suppliers)}" if overdue_suppliers else ""
    detail = f"${overdue_value:,.0f} owed to suppliers past its due date across {len(overdue)} bill(s){names}."

    if len(overdue) >= SUPPLIER_ATTENTION_COUNT:
        return FlagDecision("supplier_relationship", "attention", f"{len(overdue)} supplier bills overdue", detail, metric)
    if overdue:
        return FlagDecision("supplier_relationship", "watch", f"{len(overdue)} supplier bill(s) overdue", detail, metric)
    return FlagDecision("supplier_relationship", "ok", "No supplier bills overdue", "All payables current.", metric)


def decide_payables_backlog(bills: list[dict]) -> FlagDecision:
    """Honest substitute for 'inventory levels' — see module docstring. Reads
    the same COLLECTION_BILLS data as decide_supplier_relationship but as an
    aggregate working-capital view, not a per-vendor relationship view.

    Entity-naming (2026-08-10): names which supplier(s) the unpaid value sits
    with, capped at NAMED_ENTITY_CAP, same discipline as the other 2
    per-record dimensions."""
    unpaid = [b for b in bills if b.get("status") != "paid"]
    value = sum(b.get("amount", 0) for b in unpaid)
    unpaid_suppliers = [b.get("supplier", "unknown") for b in unpaid]
    metric = {"unpaid_count": len(unpaid), "unpaid_value": round(value, 2), "unpaid_suppliers": unpaid_suppliers}
    names = f" — {_named_entities(unpaid_suppliers)}" if unpaid_suppliers else ""
    detail = f"{len(unpaid)} bill(s) totalling ${value:,.0f} not yet cleared{names}."

    if value >= PAYABLES_ATTENTION_VALUE:
        return FlagDecision("payables_backlog", "attention", f"${value:,.0f} tied up in unpaid bills", detail, metric)
    if value > 0:
        return FlagDecision("payables_backlog", "watch", f"${value:,.0f} tied up in unpaid bills", detail, metric)
    return FlagDecision("payables_backlog", "ok", "No unpaid bills outstanding", "Payables clear.", metric)


def run_cycle(db, gateway, audit_logger, now: datetime | None = None) -> list[dict]:
    """`now` (2026-08-10, same fix as outreach_check/payment_followup
    run_cycle): defaults to real wall-clock time in production; injectable
    only so a test can pin a deterministic reference point instead of a
    seeded fixture's trailing-window assumptions silently drifting as real
    calendar days pass."""
    client = gateway.client_for("analytics")
    now = now or datetime.now(UTC)

    cash_events = [doc.to_dict() for doc in db.collection(COLLECTION_CASH_EVENTS).stream()]
    portfolio_invoices = [doc.to_dict() for doc in db.collection(COLLECTION_PORTFOLIO_INVOICES).stream()]
    engagements = [doc.to_dict() for doc in db.collection(COLLECTION_PORTFOLIO_ENGAGEMENTS).stream()]
    bills = [doc.to_dict() for doc in db.collection(COLLECTION_BILLS).stream()]
    growth_snap = db.collection(COLLECTION_GROWTH_SNAPSHOT).document("current").get()
    pipeline_snap = db.collection(COLLECTION_SALES_PIPELINE).document("current").get()

    decisions = [
        decide_cash_movements(cash_events, portfolio_invoices, now),
        decide_operational_bottlenecks(engagements, now),
        decide_growth(growth_snap.to_dict() if growth_snap.exists else {}),
        decide_sales_pipeline(pipeline_snap.to_dict() if pipeline_snap.exists else {}),
        decide_supplier_relationship(bills, now),
        decide_payables_backlog(bills),
    ]

    results = []
    for decision in decisions:
        trace_id = audit_logger.new_trace_id()
        client.call(
            "record_flag",
            trace_id=trace_id,
            dimension=decision.dimension,
            severity=decision.severity,
            headline=decision.headline,
            detail=decision.detail,
            metric=decision.metric,
        )
        results.append({"dimension": decision.dimension, "severity": decision.severity, "headline": decision.headline})
    return results


__all__ = [
    "ANALYTICS_SCOPE",
    "FlagDecision",
    "decide_cash_movements",
    "decide_growth",
    "decide_operational_bottlenecks",
    "decide_payables_backlog",
    "decide_sales_pipeline",
    "decide_supplier_relationship",
    "run_cycle",
]
