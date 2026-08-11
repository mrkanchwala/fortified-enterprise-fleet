"""Agent Registry — Fleet track's Discovery & Lifecycle component.

Persists each agent's declared CapabilityScope to Firestore so the declaration
is independently inspectable (dashboard, audits) and not just an in-memory
constant. The Gateway is the enforcement point; this module is the record of
what was declared.
"""

from datetime import UTC, datetime

from fleet_hackathon.capability import CapabilityScope
from fleet_hackathon.config import COLLECTION_REGISTRY


class AgentRegistry:
    def __init__(self, db):
        self._db = db

    def declare(self, scope: CapabilityScope) -> None:
        """Idempotent re-declaration (e.g. every cold start) must not clobber
        a human's enable/disable toggle (set_enabled) — only the scope/actions/
        criterion get refreshed from code; `enabled` carries over from whatever
        is already in Firestore, if anything is."""
        existing = self._db.collection(COLLECTION_REGISTRY).document(scope.agent_name).get()
        doc = scope.to_dict()
        if existing.exists:
            doc["enabled"] = existing.to_dict().get("enabled", scope.enabled)
        doc["declared_at"] = datetime.now(UTC).isoformat()
        self._db.collection(COLLECTION_REGISTRY).document(scope.agent_name).set(doc)

    def set_enabled(self, agent_name: str, enabled: bool) -> None:
        """The human-flipped governance switch — the only way `enabled` ever
        changes. The system itself never calls this."""
        self._db.collection(COLLECTION_REGISTRY).document(agent_name).set({"enabled": enabled}, merge=True)

    def get(self, agent_name: str) -> CapabilityScope | None:
        snap = self._db.collection(COLLECTION_REGISTRY).document(agent_name).get()
        if not snap.exists:
            return None
        return CapabilityScope.from_dict(snap.to_dict())

    def list_all(self) -> list[dict]:
        return [doc.to_dict() for doc in self._db.collection(COLLECTION_REGISTRY).stream()]
