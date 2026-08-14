# Fortified Enterprise Fleet

A multi-agent back-office fleet for SMB operations, built for the All Things Agentic hackathon (Fortified Enterprise Fleet track).

## Live demo

- https://quadrigasolutions.com/agent-fleet/
- Direct Cloud Run URL: https://agent-fleet-758180534444.us-central1.run.app

## What it does

Five specialized agents run against a shared CRM dataset: Outreach-Check, Invoice, Payment-Followup, Account-Management, and Analytics. Each agent sits behind a common Registry, Gateway, and Model Armor layer, with OpenTelemetry tracing across every run. A dashboard shows the fleet's current state (kanban view) and an audit trail of every action taken.

Every agent is human-gated: an operator flips it on or off, and the system never enables, disables, or otherwise modifies its own fleet. Account-Management's delivery-status actions fail closed rather than guess. The fleet's own runtime state (agent status, audit log, cash events) lives in Firestore, refreshed on a schedule so the dashboard reflects a live, moving system rather than a static demo.

## Architecture

Single FastAPI service on Cloud Run:

- `GET /` and `GET /health` — public, read-only dashboard.
- `POST /run/{agent_name}` and `POST /tick` — gated by an `X-Fleet-Runtime-Token` header, checked with a constant-time comparison, and rate-limited per client IP (in-memory sliding window, backed by an nginx flood guard in front of it).
- Cloud Scheduler triggers `/tick` every 30 minutes to keep the demo data moving.
- Firestore holds fleet state, audit log, and demo CRM records. The audit log self-prunes on a cap so it can't grow unbounded.

## Stack

Google ADK, Gemini, FastAPI, Firestore, Cloud Run, Cloud Scheduler, OpenTelemetry, uv.

## Run locally

```bash
uv sync
export GOOGLE_CLOUD_PROJECT=<your-gcp-project>
uv run uvicorn fleet_hackathon.app:app --reload
```

## Tests

```bash
uv run pytest
```

## Built by

[Quadriga Automations](https://quadrigasolutions.com) — AI automation infrastructure for marketing, sales, operations, and engineering teams.

## License

MIT. See [LICENSE](LICENSE).
