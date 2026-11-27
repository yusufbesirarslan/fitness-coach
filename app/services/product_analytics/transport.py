"""Synthetic transport only.

The dispatcher talks to a transport object with ``send(body: bytes)`` and
``delete_subjects(subjects: list[str])``. This source ships ONLY the in-memory
synthetic implementation. A PostHog EU network transport requires the separate
vendor (DPA/project/token) and activation authorization and is intentionally
absent, so no configuration value can make this code reach a vendor.
"""


class TransportFailure(Exception):
    """The destination did not acknowledge; retry under the current grant."""


class SyntheticTransport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []
        self.deleted = []

    def send(self, body):
        if self.fail:
            raise TransportFailure()
        self.sent.append(bytes(body))

    def delete_subjects(self, subjects):
        if self.fail:
            raise TransportFailure()
        self.deleted.append(list(subjects))
