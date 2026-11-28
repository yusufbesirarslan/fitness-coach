"""PostHog EU Cloud HTTPS transport: production-capable, default-unreachable.

The ONLY module in this package allowed to import an HTTP client
(architecture test). It is constructed exclusively by ``build`` from complete
``settings.VendorSettings``; nothing in this source provides those values,
so no deployment reaches a vendor until separately authorized configuration
exists. It runs only inside the worker's analytics pass, never in a request.

Exact destination allowlist (code constants; config selects region only):

* capture   POST https://eu.i.posthog.com/batch/
* persons   GET  https://eu.posthog.com/api/projects/<id>/persons/?distinct_id=
* delete    POST https://eu.posthog.com/api/projects/<id>/persons/bulk_delete/
* verify    GET  https://eu.posthog.com/api/projects/<id>/persons/deletion_status/

Contract source: posthog.com/docs/api (EU public vs private hosts),
/docs/api/capture (batch), /docs/privacy/data-storage (personless events
"have no person to delete"; documented workaround: create a profile, then
bulk_delete with distinct_ids + delete_events; 202 accepted; asynchronous,
off-peak deletion; deletion_status with delete_verified_at). Response shapes
are parsed defensively and anything unexpected is a non-success. Successful
personless-event deletion is NOT claimed until the authorized vendor E2E
(gate G5) proves it.

Requests: TLS verified, no redirects, no proxies/netrc from the environment,
bounded connect/read timeouts plus an overall deadline, no transport-level
retries (the outbox owns bounded retries), response bodies capped at 64 KiB.
Logs and exceptions carry fixed outcome codes only: never a URL with query,
token, pseudonym, body or response text.
"""
import json
import time

import requests
from requests.adapters import HTTPAdapter

from app.services.product_analytics.transport import TransportFailure

CAPTURE_ORIGIN = "https://eu.i.posthog.com"
API_ORIGIN = "https://eu.posthog.com"
BATCH_URL = CAPTURE_ORIGIN + "/batch/"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_DELETE_BATCH = 50
_RETRYABLE = frozenset({408, 425, 429, 500, 502, 503, 504})


def _new_session():
    session = requests.Session()
    session.trust_env = False          # no HTTP(S)_PROXY, netrc or CA overrides
    adapter = HTTPAdapter(max_retries=0, pool_connections=2, pool_maxsize=2)
    session.mount("https://", adapter)
    session.mount("http://", _RefuseAdapter())
    return session


class _RefuseAdapter(HTTPAdapter):
    def send(self, *args, **kwargs):  # plain HTTP is never a destination
        raise TransportFailure("destination_refused")


class PostHogTransport:
    can_capture = True

    def __init__(self, settings, *, session=None):
        self._settings = settings
        self._session = session or _new_session()
        self.api_key = settings.project_token
        self.can_erase = settings.can_erase
        project = settings.project_id
        base = f"{API_ORIGIN}/api/projects/{project}/persons/" if project else None
        self._allowed = {BATCH_URL}
        if base:
            self._persons = base
            self._bulk_delete = base + "bulk_delete/"
            self._deletion_status = base + "deletion_status/"
            self._allowed |= {self._persons, self._bulk_delete, self._deletion_status}

    def __repr__(self):
        return "PostHogTransport(region=eu)"

    # ── HTTP core ────────────────────────────────────────────────────────
    def _request(self, method, url, *, body=None, params=None, private=False):
        if url not in self._allowed:
            raise TransportFailure("destination_refused")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "axisai-product-analytics/2"}
        if private:
            if not self.can_erase:
                raise TransportFailure("not_configured")
            headers["Authorization"] = "Bearer " + self._settings.personal_api_key
        timeout = (self._settings.connect_timeout, self._settings.read_timeout)
        deadline = time.monotonic() + sum(timeout)
        try:
            response = self._session.request(
                method, url, data=body, params=params, headers=headers, timeout=timeout,
                allow_redirects=False, stream=True, verify=True)
            try:
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise TransportFailure("response_too_large")
                    if time.monotonic() > deadline:
                        raise TransportFailure("timeout")
                    chunks.append(chunk)
                status = response.status_code
            finally:
                response.close()
        except TransportFailure:
            raise
        except requests.Timeout:
            raise TransportFailure("timeout") from None
        except requests.RequestException:
            raise TransportFailure("network") from None
        if 300 <= status < 400:
            raise TransportFailure("redirect_refused")
        if status in _RETRYABLE:
            raise TransportFailure("vendor_retry")
        if status >= 400:
            raise TransportFailure("vendor_rejected")
        return status, b"".join(chunks)

    @staticmethod
    def _json(raw):
        try:
            value = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            raise TransportFailure("malformed_response") from None
        return value

    # ── Capture ──────────────────────────────────────────────────────────
    def send(self, body):
        status, _ = self._request("POST", BATCH_URL, body=body)
        if status != 200:
            raise TransportFailure("unexpected_status")

    # ── Erasure (private API, personal key) ──────────────────────────────
    def find_person(self, subject):
        status, raw = self._request("GET", self._persons if self.can_erase else "",
                                    params={"distinct_id": subject}, private=True)
        value = self._json(raw)
        results = value.get("results") if isinstance(value, dict) else None
        if status != 200 or not isinstance(results, list):
            raise TransportFailure("malformed_response")
        def ref(item):
            # The person UUID is what deletion_status is keyed by; accept
            # either a "uuid" field or a string "id" (shape verified at G5).
            for key in ("uuid", "id"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value[:64]
            return None
        matches = [item for item in results
                   if isinstance(item, dict) and ref(item) is not None
                   and isinstance(item.get("distinct_ids"), list)
                   and subject in item["distinct_ids"]]
        if len(matches) > 1:
            raise TransportFailure("ambiguous_person")
        return ref(matches[0]) if matches else None

    def bulk_delete(self, subjects):
        subjects = list(subjects)
        if not subjects or len(subjects) > MAX_DELETE_BATCH:
            raise TransportFailure("invalid_batch")
        body = json.dumps({"distinct_ids": subjects, "delete_events": True},
                          separators=(",", ":")).encode("utf-8")
        status, raw = self._request("POST", self._bulk_delete if self.can_erase else "",
                                    body=body, private=True)
        if status != 202:
            raise TransportFailure("unexpected_status")
        value = self._json(raw)
        if not isinstance(value, dict):
            raise TransportFailure("malformed_response")
        counts = {}
        for key in ("persons_found", "persons_queued_for_deletion", "persons_deleted"):
            item = value.get(key, 0)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise TransportFailure("malformed_response")
            counts[key] = item
        errors = value.get("deletion_errors", [])
        counts["deletion_errors"] = errors if isinstance(errors, list) else ["malformed"]
        return counts

    def deletion_verified(self, ref):
        status, raw = self._request("GET", self._deletion_status if self.can_erase else "",
                                    params={"person_uuid": ref, "status": "all"}, private=True)
        value = self._json(raw)
        results = value.get("results") if isinstance(value, dict) else value
        if status != 200 or not isinstance(results, list):
            raise TransportFailure("malformed_response")
        return any(isinstance(item, dict) and item.get("person_uuid") == ref
                   and bool(item.get("delete_verified_at")) for item in results)


def build(settings, *, session=None):
    """The network transport for complete settings, else None (fail closed)."""
    return PostHogTransport(settings, session=session) if settings is not None else None
