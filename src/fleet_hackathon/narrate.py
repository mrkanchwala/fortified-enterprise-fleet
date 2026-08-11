"""Optional Gemini narration layer, isolated from the decision logic on purpose.

Every agent's `decide()` function (agents/*.py) is pure Python — no LLM call,
fully unit-testable, and the actual enforcement path (Gateway/CapabilityScope)
never depends on this module. This is where the one deliberate, real Gemini
call per triggered case happens, turning a structured decision into the
plain-English narration the demo/dashboard shows — matching the standing
cost-discipline rule (`feedback_minimize-billable-gcp-api-calls-fleet-hackathon.md`):
exactly one call per real question, no iterate-until-it-feels-right loop.

Wired against the ADK 2.6.2 API confirmed live against the installed package
(InMemorySessionService.create_session is async, Runner.run is a sync
generator of Events, Event.is_final_response() exists) — not yet exercised
with a real Gemini call. Do exactly one live smoke-test call before Step 7a
uses this in the demo, per the same cost-discipline rule that governs it.
Failures here are caught and degrade to no narration — an LLM hiccup must
never block or mask the deterministic dispatch/audit path.
"""

import asyncio
import logging

from google.adk.agents.llm_agent import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

logger = logging.getLogger(__name__)

_APP_NAME = "fleet_narrator"
_USER_ID = "fleet-runtime"

_narration_agent = Agent(
    model="gemini-3.5-flash",
    name="narrator",
    description="Explains one autonomous agent decision in one plain-English sentence.",
    instruction=(
        "You are given one autonomous agent's decision as structured facts. "
        "Write exactly one plain-English sentence explaining the decision and "
        "why, for a technical judge reading an audit log. No preamble, no "
        "markdown, no restating the input verbatim."
    ),
)


async def _narrate_async(decision_summary: str) -> str:
    runner = InMemoryRunner(agent=_narration_agent, app_name=_APP_NAME)
    session = await runner.session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID)
    message = types.Content(role="user", parts=[types.Part(text=decision_summary)])

    final_text = ""
    for event in runner.run(user_id=_USER_ID, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(part.text or "" for part in event.content.parts)
    return final_text.strip()


def narrate(decision_summary: str) -> str | None:
    """Returns None (never raises) on any failure — narration is a demo
    enhancement, not a dependency of the enforced dispatch path."""
    try:
        return asyncio.run(_narrate_async(decision_summary))
    except Exception:
        logger.exception("narration call failed, continuing without it")
        return None
