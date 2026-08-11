"""Cash-cycle and account-manager analytics — pure functions over invoice
dicts, no Firestore/agent coupling, so they're testable in isolation and
reusable by both the dashboard and any future report.

Benchmarks below are real, sourced 2026 figures (dashboard cites them
inline, not just here) — never presented as live/authoritative, always
labeled as external benchmarks a synthetic demo portfolio is compared
against:
- DSO mid-market benchmark: 52 days (Eagle Rock CFO / Billtrust 2026 AR
  Benchmark Report; overall best-in-class B2B benchmark is 39 days).
- AR turnover ratio: 5-12x typical for B2B, 12+ for subscription/SaaS
  (ARDEM, Serrala 2026 AR KPI reports).
- Bad-debt ratio target: under 1.5-2% of credit sales for B2B (ARDEM 2026).
- Risk-free short-term rate for opportunity-cost framing: 3.82% (US 3-Month
  Treasury Bill yield, Aug 6 2026, Trading Economics) — used only to
  illustrate the foregone-return cost of cash sitting in receivables, not
  a claim about actual investable return.
"""

from datetime import UTC, datetime

DSO_BENCHMARK_DAYS = 52.0
AR_TURNOVER_BENCHMARK_LOW = 5.0
AR_TURNOVER_BENCHMARK_HIGH = 12.0
BAD_DEBT_BENCHMARK_PCT = 1.5
RISK_FREE_ANNUAL_RATE = 0.0382


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def total_invoiced(invoices: list[dict]) -> float:
    return sum(inv.get("amount", 0) for inv in invoices)


def total_collected(invoices: list[dict]) -> float:
    return sum(inv.get("amount_paid", 0) or 0 for inv in invoices)


def total_outstanding(invoices: list[dict]) -> float:
    return sum(
        max(inv.get("amount", 0) - (inv.get("amount_paid", 0) or 0), 0)
        for inv in invoices
        if inv.get("status") != "paid"
    )


def collection_rate(invoices: list[dict]) -> float | None:
    invoiced = total_invoiced(invoices)
    if invoiced <= 0:
        return None
    return total_collected(invoices) / invoiced


def average_days_to_pay(invoices: list[dict]) -> float | None:
    """Average days between issue and payment, for invoices actually paid."""
    deltas = []
    for inv in invoices:
        issued = _parse(inv.get("issued_ts") or inv.get("issue_date"))
        paid = _parse(inv.get("paid_ts") or inv.get("paid_date"))
        if issued and paid:
            deltas.append((paid - issued).days)
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


def dso(invoices: list[dict], now: datetime | None = None, period_days: int = 90) -> float | None:
    """Standard DSO approximation: (ending AR / total credit sales in the
    period) x period_days. `now` defaults to current time; pass explicitly
    in tests for determinism."""
    now = now or datetime.now(UTC)
    invoiced = total_invoiced(invoices)
    if invoiced <= 0:
        return None
    outstanding = total_outstanding(invoices)
    return (outstanding / invoiced) * period_days


def ar_turnover(invoices: list[dict]) -> float | None:
    """Total credit sales / average AR (approximated here as current
    outstanding AR, since we don't have a prior-period balance in a demo
    dataset)."""
    outstanding = total_outstanding(invoices)
    if outstanding <= 0:
        return None
    return total_invoiced(invoices) / outstanding


def manager_breakdown(invoices: list[dict]) -> list[dict]:
    """One row per account manager: deal count, invoiced, collected,
    collection rate, average days to pay — the exact shape needed to spot
    'closed a lot, collects poorly' patterns."""
    by_manager: dict[str, list[dict]] = {}
    for inv in invoices:
        manager = inv.get("account_manager", "Unassigned")
        by_manager.setdefault(manager, []).append(inv)

    rows = []
    for manager, mgr_invoices in by_manager.items():
        rows.append(
            {
                "account_manager": manager,
                "deal_count": len(mgr_invoices),
                "total_invoiced": total_invoiced(mgr_invoices),
                "total_collected": total_collected(mgr_invoices),
                "collection_rate": collection_rate(mgr_invoices),
                "average_days_to_pay": average_days_to_pay(mgr_invoices),
            }
        )
    return sorted(rows, key=lambda r: r["deal_count"], reverse=True)


def opportunity_cost(outstanding_ar: float, annual_rate: float = RISK_FREE_ANNUAL_RATE) -> float:
    """Illustrative annualized foregone return if the same cash were parked
    at the risk-free rate instead of sitting in unpaid receivables."""
    return outstanding_ar * annual_rate


def underperforming_managers(rows: list[dict], min_deals: int = 3, collection_floor: float = 0.7) -> list[dict]:
    """Flag managers with real deal volume but a collection rate below the
    floor — the 'closed a lot, collects poorly' pattern, not just low
    volume (which isn't inherently a problem)."""
    return [
        row
        for row in rows
        if row["deal_count"] >= min_deals
        and row["collection_rate"] is not None
        and row["collection_rate"] < collection_floor
    ]
