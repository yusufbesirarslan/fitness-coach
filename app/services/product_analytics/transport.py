"""Transport contract and the synthetic (in-memory) vendor.

The dispatcher and the erasure processor talk to a transport object with:

* ``api_key``                   project token placed in the batch body
* ``send(body: bytes)``         POST one ``/batch/`` body (capture)
* ``find_person(subject)``      vendor person reference for a pseudonym, or None
* ``bulk_delete(subjects)``     request deletion of persons + their events
* ``deletion_verified(ref)``    True only when the vendor reports it verified

``SyntheticTransport`` models that contract in memory for tests and local
qualification. The only network implementation is
``posthog_transport.PostHogTransport``; it is constructed only from complete,
validated configuration (``settings``) and nothing in this source supplies
that configuration.
"""
import json
import uuid


class TransportFailure(Exception):
    """The destination did not acknowledge; retry later under the current grant.

    ``outcome`` is a fixed short code (never a response body, URL or token)."""

    def __init__(self, outcome="unavailable"):
        super().__init__(outcome)
        self.outcome = outcome


SYNTHETIC_API_KEY = "phc_SYNTHETIC_NOT_A_PROJECT"


class SyntheticTransport:
    """In-memory vendor: records bodies, models profiles, deletion and verification."""

    can_capture = True
    can_erase = True

    def __init__(self, *, fail=False, ingest_profiles=True):
        self.api_key = SYNTHETIC_API_KEY
        self.fail = fail
        self.ingest_profiles = ingest_profiles
        self.sent = []
        self.deleted = []
        self.persons = {}
        self.verified = set()

    def _check(self):
        if self.fail:
            raise TransportFailure("synthetic_failure")

    def send(self, body):
        self._check()
        self.sent.append(bytes(body))
        for item in json.loads(body)["batch"]:
            if item["event"] == "$set" and self.ingest_profiles:
                self.persons.setdefault(item["distinct_id"], str(uuid.uuid4()))

    def find_person(self, subject):
        self._check()
        return self.persons.get(subject)

    def bulk_delete(self, subjects):
        self._check()
        self.deleted.append(list(subjects))
        found = [s for s in subjects if s in self.persons]
        return {"persons_found": len(found), "persons_queued_for_deletion": len(found),
                "persons_deleted": 0, "deletion_errors": []}

    def deletion_verified(self, ref):
        self._check()
        return ref in self.verified

    def verify_all(self):
        """Test helper: the vendor's asynchronous deletion batch has run."""
        self.verified.update(self.persons.values())
