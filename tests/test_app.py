"""FastAPI route-layer tests (2026-08-10, CSO HIGH fix) — rate limiting and
the auth-token check. Scoped to routes/paths that never reach get_db(), since
app.py's routes call the real Firestore client directly (not
dependency-injected) — /healthz for the public bucket, and a wrong/missing
token on a mutating route for the mutating bucket, since both the rate-limit
429 and the auth 403 short-circuit before any Firestore call happens."""

import pytest
from fastapi.testclient import TestClient

from fleet_hackathon import app as app_module

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """The in-memory rate-limit log is a module-level global — clear it
    before every test so tests don't bleed into each other's counts."""
    app_module._request_log.clear()
    yield
    app_module._request_log.clear()


def test_public_route_allows_requests_under_the_limit():
    for _ in range(app_module._RATE_LIMITS["public"]):
        response = client.get("/healthz")
        assert response.status_code == 200


def test_public_route_blocks_once_over_the_limit():
    limit = app_module._RATE_LIMITS["public"]
    for _ in range(limit):
        client.get("/healthz")

    response = client.get("/healthz")
    assert response.status_code == 429


def test_public_route_rate_limit_is_scoped_per_ip():
    limit = app_module._RATE_LIMITS["public"]
    for _ in range(limit):
        client.get("/healthz", headers={"x-forwarded-for": "1.1.1.1"})

    # a different caller (different X-Forwarded-For) must not be blocked
    response = client.get("/healthz", headers={"x-forwarded-for": "2.2.2.2"})
    assert response.status_code == 200


def test_mutating_route_missing_token_returns_403_not_429_under_the_limit():
    response = client.post("/run/outreach_check")
    assert response.status_code == 403


def test_mutating_route_blocks_with_429_once_over_the_limit():
    """Sent with no token — every one of these 403s before reaching the DB,
    confirming the rate-limit check itself (not just auth) is real."""
    limit = app_module._RATE_LIMITS["mutating"]
    for _ in range(limit):
        client.post("/run/outreach_check")

    response = client.post("/run/outreach_check")
    assert response.status_code == 429


def test_token_check_uses_constant_time_comparison(monkeypatch):
    """Regression guard: a plain `!=` would still functionally reject a wrong
    token, so this asserts the actual comparison call goes through
    hmac.compare_digest rather than re-deriving timing behavior (which isn't
    reliably testable in a unit test)."""
    import hmac

    monkeypatch.setenv("FLEET_RUNTIME_TOKEN", "correct-token")
    calls = []
    real_compare = hmac.compare_digest

    def _spy(*args, **kwargs):
        calls.append(args)
        return real_compare(*args, **kwargs)

    monkeypatch.setattr(app_module.hmac, "compare_digest", _spy)
    client.post("/run/outreach_check", headers={"x-fleet-runtime-token": "wrong-token"})
    assert calls, "hmac.compare_digest must be the mechanism used to check the token"


def test_auth_failure_is_logged(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="fleet_hackathon.app"):
        client.post("/run/outreach_check", headers={"x-forwarded-for": "9.9.9.9"})
    assert any("rejected request" in record.message and "9.9.9.9" in record.message for record in caplog.records)
