"""Real Firestore client factory. The only module that imports google.cloud.firestore
directly — everything else (capability/registry/telemetry/gateway/actions) takes a
`db` object as a constructor/function argument, so tests can pass a fake instead."""

from google.cloud import firestore

from fleet_hackathon.config import GCP_PROJECT

_client: firestore.Client | None = None


def get_db() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=GCP_PROJECT)
    return _client
