"""FastAPI entrypoint — single Cloud Run service hosting both the public
read-only dashboard and the Cloud-Scheduler-triggered agent run endpoints, per
the build plan's single-service architecture call.

Route security model (deliberate, since Cloud Run's `--allow-unauthenticated`
is service-wide, not per-route, and the plan commits to one service):
- `GET /` and `GET /healthz` — public, read-only, no secret required.
- `POST /run/{agent_name}` — requires the `X-Fleet-Runtime-Token` header to
  match `FLEET_RUNTIME_TOKEN` (sourced from Secret Manager at deploy time,
  never hardcoded — see Step 13's README credential-handling section). Cloud
  Scheduler's HTTP target config sets this header; the dashboard page never
  links to or exposes this route to a visitor.

Rate limiting (2026-08-10, CSO HIGH finding fix): once deployed, this URL is
public and judge-discoverable, and nothing previously throttled repeated
requests to any route — including the token-gated mutating ones, which
combined with a non-constant-time token comparison made a brute-force attempt
theoretically practical. Fixed with a minimal in-memory sliding-window
limiter (no new dependency — matches this codebase's existing preference for
small, testable, self-contained logic over pulling in a library for a
narrowly-scoped need, e.g. model_armor.py). Caveat, stated honestly: this is
per-instance, in-memory state — correct for a scale-to-zero demo service that
runs as one instance most of the time, but not a substitute for an
infra-layer control (Cloud Armor) if this were carrying real traffic. The
token comparison now uses `hmac.compare_digest` (constant-time) instead of
`!=`, and every failed token check is logged with the caller's IP so a
brute-force attempt would actually be visible.
"""

import hmac
import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from fleet_hackathon import actions, dashboard, runtime
from fleet_hackathon.firestore_client import get_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    runtime.ensure_registered(get_db())
    yield


app = FastAPI(title="Fortified Enterprise Fleet", lifespan=_lifespan)

# --- Rate limiting ---------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS = {
    "public": 30,  # GET / and /healthz — dashboard viewing
    "mutating": 10,  # the 4 token-gated POST routes
}
_request_log: dict[tuple[str, str], list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    """Cloud Run sits behind Google's load balancer — the real caller is the
    first hop in X-Forwarded-For, not request.client.host (that's the LB)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request, bucket: str) -> None:
    limit = _RATE_LIMITS[bucket]
    ip = _client_ip(request)
    key = (bucket, ip)
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS

    recent = [t for t in _request_log[key] if t > window_start]
    if len(recent) >= limit:
        logger.warning(f"rate limit exceeded: bucket={bucket} ip={ip}")
        raise HTTPException(status_code=429, detail="rate limit exceeded, try again shortly")
    recent.append(now)
    _request_log[key] = recent


# --- Auth --------------------------------------------------------------


def _require_runtime_token(request: Request, x_fleet_runtime_token: str | None) -> None:
    expected = os.environ.get("FLEET_RUNTIME_TOKEN")
    if not expected or not x_fleet_runtime_token or not hmac.compare_digest(x_fleet_runtime_token, expected):
        logger.warning(f"rejected request: missing or invalid runtime token, ip={_client_ip(request)}")
        raise HTTPException(status_code=403, detail="missing or invalid runtime token")


@app.get("/healthz")
def healthz(request: Request) -> dict:
    _check_rate_limit(request, "public")
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    _check_rate_limit(request, "public")
    return dashboard.render(get_db())


@app.post("/run/{agent_name}")
def run_agent(agent_name: str, request: Request, x_fleet_runtime_token: str | None = Header(default=None)) -> dict:
    _check_rate_limit(request, "mutating")
    _require_runtime_token(request, x_fleet_runtime_token)
    try:
        results = runtime.run_agent_cycle(get_db(), agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"agent": agent_name, "results": results}


@app.post("/run-all")
def run_all(request: Request, x_fleet_runtime_token: str | None = Header(default=None)) -> dict:
    _check_rate_limit(request, "mutating")
    _require_runtime_token(request, x_fleet_runtime_token)
    return runtime.run_all_cycles(get_db())


@app.post("/mark-paid/{invoice_id}")
def mark_paid(invoice_id: str, request: Request, x_fleet_runtime_token: str | None = Header(default=None)) -> dict:
    """Simulates a payment landing (a real deployment would wire this to a
    payment-processor webhook) — not an agent action, so it bypasses the
    Gateway entirely, same as seed_demo_data.py injecting initial state.
    Demo use: trigger this live, then /run payment_followup (shows
    'already paid') and /run account_management (hands off) to close the
    loop on camera."""
    _check_rate_limit(request, "mutating")
    _require_runtime_token(request, x_fleet_runtime_token)
    try:
        result = actions.record_payment(get_db(), invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@app.post("/toggle-agent/{agent_name}")
def toggle_agent(
    agent_name: str, enabled: bool, request: Request, x_fleet_runtime_token: str | None = Header(default=None)
) -> dict:
    """The one human-flipped governance switch (query param `enabled=true|false`)
    — the system never calls this on itself, only a human via this endpoint."""
    _check_rate_limit(request, "mutating")
    _require_runtime_token(request, x_fleet_runtime_token)
    try:
        runtime.set_agent_enabled(get_db(), agent_name, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"agent": agent_name, "enabled": enabled}
