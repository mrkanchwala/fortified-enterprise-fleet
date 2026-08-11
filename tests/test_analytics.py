"""Cash-cycle and account-manager analytics — pure functions, no Firestore.
The exact numbers a CFO/COO would read off the Executive Snapshot."""

from datetime import UTC, datetime

from fleet_hackathon import analytics

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _invoice(amount, amount_paid=0, issued_days_ago=30, paid_days_after_issue=None, manager="Test Manager"):
    issued = NOW.replace(hour=0)
    issued_ts = issued.isoformat()
    paid_ts = None
    if paid_days_after_issue is not None:
        from datetime import timedelta

        paid_ts = (issued + timedelta(days=paid_days_after_issue)).isoformat()
    return {
        "amount": amount,
        "amount_paid": amount_paid,
        "status": "paid" if paid_ts else "issued",
        "issued_ts": issued_ts,
        "paid_ts": paid_ts,
        "account_manager": manager,
    }


def test_total_invoiced_and_collected():
    invoices = [_invoice(1000, 1000, paid_days_after_issue=5), _invoice(500)]
    assert analytics.total_invoiced(invoices) == 1500
    assert analytics.total_collected(invoices) == 1000


def test_collection_rate_partial():
    invoices = [_invoice(1000, 1000, paid_days_after_issue=5), _invoice(1000)]
    assert analytics.collection_rate(invoices) == 0.5


def test_collection_rate_none_when_nothing_invoiced():
    assert analytics.collection_rate([]) is None


def test_average_days_to_pay_only_counts_paid_invoices():
    invoices = [
        _invoice(1000, 1000, paid_days_after_issue=10),
        _invoice(1000, 1000, paid_days_after_issue=20),
        _invoice(1000),  # unpaid, excluded
    ]
    assert analytics.average_days_to_pay(invoices) == 15


def test_manager_breakdown_separates_managers():
    invoices = [
        _invoice(1000, 1000, paid_days_after_issue=5, manager="Anya Petrov"),
        _invoice(1000, manager="Anya Petrov"),
        _invoice(500, 500, paid_days_after_issue=3, manager="Nadia Larsson"),
    ]
    rows = analytics.manager_breakdown(invoices)
    by_name = {r["account_manager"]: r for r in rows}

    assert by_name["Anya Petrov"]["deal_count"] == 2
    assert by_name["Anya Petrov"]["collection_rate"] == 0.5
    assert by_name["Nadia Larsson"]["deal_count"] == 1
    assert by_name["Nadia Larsson"]["collection_rate"] == 1.0


def test_underperforming_managers_flags_high_volume_low_collection():
    rows = [
        {"account_manager": "Anya Petrov", "deal_count": 8, "collection_rate": 0.45,
         "total_invoiced": 100, "total_collected": 45, "average_days_to_pay": 40},
        {"account_manager": "Nadia Larsson", "deal_count": 5, "collection_rate": 0.9,
         "total_invoiced": 50, "total_collected": 45, "average_days_to_pay": 10},
        {"account_manager": "Low Volume Rep", "deal_count": 1, "collection_rate": 0.2,
         "total_invoiced": 10, "total_collected": 2, "average_days_to_pay": 60},
    ]
    flagged = analytics.underperforming_managers(rows, min_deals=3, collection_floor=0.7)
    names = {f["account_manager"] for f in flagged}

    assert "Anya Petrov" in names, "real volume + poor collection must be flagged"
    assert "Nadia Larsson" not in names, "good collection rate must not be flagged"
    assert "Low Volume Rep" not in names, "low volume alone is not inherently a problem"


def test_opportunity_cost_scales_with_outstanding_and_rate():
    assert analytics.opportunity_cost(100_000, annual_rate=0.04) == 4_000


def test_dso_zero_when_fully_collected():
    invoices = [_invoice(1000, 1000, paid_days_after_issue=5)]
    assert analytics.dso(invoices, now=NOW) == 0


def test_dso_positive_when_outstanding_exists():
    invoices = [_invoice(1000)]
    result = analytics.dso(invoices, now=NOW, period_days=90)
    assert result == 90  # 100% outstanding of total invoiced -> full period


def test_ar_turnover_none_when_no_outstanding():
    invoices = [_invoice(1000, 1000, paid_days_after_issue=1)]
    assert analytics.ar_turnover(invoices) is None
