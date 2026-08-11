"""The dashboard reads live from Firestore (the fake, here) — never a static
mock. Rendering before and after an agent cycle must actually differ."""

from datetime import UTC, datetime

from fleet_hackathon import dashboard, runtime, seed_demo_data

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)

_FULL_LOG_EMPTY_TEXT = "No activity yet — trigger a run to see it here."


def _seeded_db(fake_db):
    seed_demo_data.seed(fake_db, now=NOW)
    runtime.ensure_registered(fake_db)
    return fake_db


def test_dashboard_shows_all_five_declared_agents(fake_db):
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    for agent_name in ("outreach_check", "invoice", "payment_followup", "account_management", "analytics"):
        assert agent_name in html


def test_dashboard_shows_analytics_flags_after_a_cycle(fake_db):
    db = _seeded_db(fake_db)
    before = dashboard.render(db)
    assert "No analytics run yet" in before

    runtime.run_agent_cycle(db, "analytics")
    after = dashboard.render(db)

    assert "What the Analytics Agent is watching" in after
    assert "Operational bottlenecks" in after
    assert "No analytics run yet" not in after


def test_dashboard_activity_log_is_empty_before_any_cycle_runs(fake_db):
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert _FULL_LOG_EMPTY_TEXT in html


def test_dashboard_reflects_live_state_after_a_cycle_not_a_static_mock(fake_db):
    db = _seeded_db(fake_db)
    before = dashboard.render(db)
    assert _FULL_LOG_EMPTY_TEXT in before

    runtime.run_agent_cycle(db, "outreach_check")
    after = dashboard.render(db)

    assert _FULL_LOG_EMPTY_TEXT not in after
    assert "Notify a team member" in after


def test_dashboard_shows_failure_catch_scenario_ready_then_caught(fake_db):
    db = _seeded_db(fake_db)
    ready = dashboard.render(db)
    assert "READY" in ready

    runtime.run_agent_cycle(db, "payment_followup")
    caught = dashboard.render(db)

    assert "CAUGHT" in caught


def test_dashboard_failure_catch_scenario_resets_to_ready_after_a_reseed(fake_db):
    """2026-08-10, Step 8 live re-check: a re-seed (the documented "run
    immediately before the final take" step) must reset the dashboard's
    READY/CAUGHT indicator back to READY, not leave it stuck on CAUGHT from
    an earlier rehearsal — found via a repeated multi-take dry run before the
    audit-log-clear fix, where a 2nd/3rd take's dashboard showed CAUGHT
    before the scenario had even been triggered on that take."""
    db = _seeded_db(fake_db)
    runtime.run_agent_cycle(db, "payment_followup")
    assert "CAUGHT" in dashboard.render(db)

    seed_demo_data.seed(db, now=NOW)  # simulates "re-run the seed script before the final take"
    reset = dashboard.render(db)
    assert "READY" in reset
    assert "CAUGHT" not in reset


def test_dashboard_shows_cash_position_from_portfolio_and_bills(fake_db):
    """2026-08-10: 'Us'/'We' renamed to the actual company name (Quadriga) —
    a bare capitalized 'Us' column header read like the USA abbreviation."""
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert "Owed to Quadriga (receivable)" in html
    assert "Quadriga owes (payable)" in html
    assert "Net cash position" in html
    assert ">Us<" not in html


def test_dashboard_flags_the_underperforming_manager(fake_db):
    """The seeded portfolio deliberately gives Anya Petrov real volume but a
    poor collection rate — the merged 'Where do we need to act?' section
    (2026-08-10 simplification pass, was 'Gaps for management to review') must
    surface it."""
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert "Anya Petrov" in html
    assert "Where do we need to act?" in html
    assert "Account managers falling behind" in html


def test_dashboard_benchmark_terms_have_hover_help(fake_db):
    """2026-08-10: 'Days Sales Outstanding' etc. are technical terms for a
    non-finance viewer — each gets a '?' badge with a hover/focus tooltip
    (CSS-only, keyboard-accessible via aria-label) explaining it in plain
    English."""
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert 'class="help"' in html
    assert "Average days between issuing an invoice and getting paid" in html


def test_dashboard_shows_financial_health_section(fake_db):
    """Cash Position + Benchmarks merged under one heading (2026-08-10)."""
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert "Financial health" in html
    assert "Are we collecting fast enough?" in html


def test_dashboard_shows_freshness_timestamp(fake_db):
    """'As of X minutes ago' (2026-08-10), sourced from the most recent
    audit-log entry — not wall-clock render time. Scoped to the
    class="freshness" span specifically, since the Activity Log's unrelated
    empty-state text ("No activity yet — trigger a run...") also contains the
    substring "No activity yet"."""
    db = _seeded_db(fake_db)
    before = dashboard.render(db)
    assert '<span class="freshness">No activity yet</span>' in before

    runtime.run_agent_cycle(db, "outreach_check")
    after = dashboard.render(db)

    assert '<span class="freshness">As of' in after
    assert '<span class="freshness">No activity yet</span>' not in after


def test_dashboard_analytics_flag_has_drill_down_after_a_cycle(fake_db):
    """Card face is headline-only (2026-08-10 simplification); the reasoning
    sentence moves into a 'Why' drill-down instead of always showing."""
    db = _seeded_db(fake_db)
    runtime.run_agent_cycle(db, "analytics")
    html = dashboard.render(db)

    assert '<summary>Why</summary>' in html
    assert "Delayed, On Hold, or overdue" in html  # the operational_bottlenecks reasoning, now tucked in the drill-down


def test_dashboard_kanban_criterion_is_tucked_behind_drill_down(fake_db):
    """2026-08-10 simplification: the full declared-criterion sentence, always
    shown before, is now a 'Declared scope' drill-down — still present (Fleet
    track Agent Identity visibility requirement) but not forced into the
    default glance."""
    db = _seeded_db(fake_db)
    html = dashboard.render(db)
    assert "<summary>Declared scope</summary>" in html
    # the sentence itself must still be present somewhere on the page
    assert "Never contacts a lead directly" in html


def test_dashboard_kanban_mini_log_caps_at_three_with_overflow_drill_down(fake_db):
    """2026-08-10 simplification: at most 3 mini-log entries show inline per
    agent; the rest collapse behind a '+N more' drill-down instead of
    repeating identical 'No action needed — OK' lines on the card face.
    Invoice/Payment-Followup/Account-Management each process 6 records
    (deal-001 + the 5 hero followup/gated invoices) after a full cycle, so
    3 overflow behind the drill-down."""
    db = _seeded_db(fake_db)
    runtime.run_all_cycles(db)
    html = dashboard.render(db)

    assert "+3 more" in html


def test_dashboard_disabled_agent_shows_disabled_badge(fake_db):
    db = _seeded_db(fake_db)
    runtime.set_agent_enabled(db, "invoice", False)
    html = dashboard.render(db)
    assert "Disabled" in html


def test_dashboard_escapes_untrusted_looking_content(fake_db):
    """Defensive check, not a real attack surface today (every rendered field
    currently comes from our own seeded/agent-written Firestore docs, not
    public input) — but the render path must not emit raw HTML for any
    audit-log field, since that's the one collection an ingested-text bug
    upstream could eventually reach."""
    db = _seeded_db(fake_db)
    from fleet_hackathon.telemetry import AuditLogger

    AuditLogger(db).log(
        trace_id="t-xss",
        agent_name="outreach_check",
        action="no_action",
        status="ok",
        detail="<script>evil()</script>",
        success_criterion="n/a",
    )
    html = dashboard.render(db)
    assert "<script>evil()</script>" not in html
    assert "&lt;script&gt;" in html
