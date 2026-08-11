"""A minimal in-memory fake implementing the subset of the google-cloud-firestore
client surface this codebase actually uses (collection/document/set/get/delete/
stream/add/order_by/limit). Lets capability/registry/telemetry/gateway/actions/
agents/dashboard all be tested without a real Firestore project or network call —
every production module in src/fleet_hackathon takes `db` as a parameter rather
than importing google.cloud.firestore directly, specifically to make this possible.
"""

import uuid

import pytest


class _FakeDocSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store, collection_name, doc_id):
        self._store = store
        self._collection_name = collection_name
        self._doc_id = doc_id

    def set(self, data, merge=False):
        coll = self._store.setdefault(self._collection_name, {})
        if merge and self._doc_id in coll:
            coll[self._doc_id] = {**coll[self._doc_id], **data}
        else:
            coll[self._doc_id] = dict(data)

    def get(self):
        coll = self._store.get(self._collection_name, {})
        return _FakeDocSnapshot(self._doc_id, coll.get(self._doc_id))

    def delete(self):
        self._store.get(self._collection_name, {}).pop(self._doc_id, None)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs  # list[(doc_id, data)]

    def order_by(self, field, direction="ASCENDING"):
        reverse = direction == "DESCENDING"
        docs = sorted(self._docs, key=lambda kv: kv[1].get(field, ""), reverse=reverse)
        return _FakeQuery(docs)

    def limit(self, n):
        return _FakeQuery(self._docs[:n])

    def stream(self):
        return [_FakeDocSnapshot(doc_id, data) for doc_id, data in self._docs]


class _FakeCollectionRef:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def document(self, doc_id):
        return _FakeDocRef(self._store, self._name, doc_id)

    def add(self, data):
        doc_id = uuid.uuid4().hex
        self.document(doc_id).set(data)
        return (None, self.document(doc_id))

    def stream(self):
        coll = self._store.get(self._name, {})
        return [_FakeDocSnapshot(doc_id, data) for doc_id, data in coll.items()]

    def order_by(self, field, direction="ASCENDING"):
        coll = self._store.get(self._name, {})
        return _FakeQuery(list(coll.items())).order_by(field, direction)


class FakeFirestore:
    def __init__(self):
        self._store: dict[str, dict[str, dict]] = {}

    def collection(self, name):
        return _FakeCollectionRef(self._store, name)


@pytest.fixture
def fake_db():
    return FakeFirestore()


@pytest.fixture
def fake_notifier():
    """Spy notifier — same shape as notifier.NullNotifier, kept separate here
    so dispatch-content tests don't depend on production defaults changing."""
    from fleet_hackathon.notifier import NullNotifier

    return NullNotifier()
